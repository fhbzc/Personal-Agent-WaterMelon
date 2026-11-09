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
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from support import TemporaryAgent
from test_final_checks import _Checked
from test_work_quick import _Quick

from personalized_agent.agent.work import handed, mail_pages

EMAIL = {"kind": "email", "title": "Reply to Ada", "to": ["ada@example.com"],
         "cc": "bo@example.com; cy@example.com", "subject": "Re: the talk & the date",
         "body": "Dear Ada,\n\n下周三可以吗？ A & B.\n\nBest,\nSam",
         "how": "Open it, read it, and send it.", "check": ["The date: Wednesday the 4th"]}
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


class AReplyInItsThreadTests(unittest.TestCase):
    """The user, 2026-11-01: a reply opened as a new message was not in the
    thread it answered. The email it answers is found among the mail pages
    the user opened - titled by their subjects - and opened there, the reply
    copied, for them to press Reply, paste and send."""

    THREAD = "https://mail.google.com/mail/u/1/#inbox/FMfcgzQbfLtQxYzWvKjHgFdSsAaPqRtU"

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        handed._threads.clear()
        self.addCleanup(handed._threads.clear)
        self.n = 0

    def _read(self, window, text, at):
        self.n += 1
        self.app.db.execute(
            "INSERT INTO raw_events (id, event_type, source, timestamp, application, data) "
            "VALUES (?, 'screen.text', 'screen', ?, 'firefox.exe', ?)",
            (f"scr_{self.n}", at, json.dumps({"window_title": window, "text": text})))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _visited(self, title, url, at="2026-10-30T09:00:00+00:00"):
        self.n += 1
        self.app.db.execute(
            "INSERT INTO raw_events (id, event_type, source, timestamp, application, data) "
            "VALUES (?, 'browser.navigation', 'browser', ?, 'firefox', ?)",
            (f"nav_{self.n}", at, json.dumps({"title": title, "url": url, "browser": "firefox"})))

    def test_kept_and_shown(self) -> None:
        item = handed.checked(dict(EMAIL, reply_to={"from": "Ada <ada@example.com>",
                                                    "subject": "The talk & the date",
                                                    "date": "2026-10-29", "else": "x"}),
                              lambda path: None)
        self.assertEqual(item["reply_to"], {"from": "Ada <ada@example.com>",
                                            "subject": "The talk & the date",
                                            "date": "2026-10-29"})
        self.assertIn("In reply to: Ada <ada@example.com> - The talk & the date - 2026-10-29",
                      handed.shown(item))
        self.assertNotIn("reply_to", handed.checked(dict(EMAIL, reply_to="x"), lambda p: None))

    def test_opened_in_the_thread_the_user_opened_it_in(self) -> None:
        self._visited("Inbox (3) - sam@example.com - Gmail",
                      "https://mail.google.com/mail/u/1/#inbox")
        self._visited("The talk & the date - other@example.com - Gmail",
                      "https://mail.google.com/mail/u/0/#inbox/QgrcJHsBmKdLpWxTnRvZyCfGhJkLmNpQ",
                      at="2026-10-31T09:00:00+00:00")
        self._read("The talk & the date - other@example.com - Gmail — Mozilla Firefox",
                   "Ada <ada@example.com>", "2026-10-31T09:01:00+00:00")
        self._visited("Re: The talk & the date - sam@example.com - Gmail", self.THREAD)
        self._read("Re: The talk & the date - sam@example.com - Gmail — Mozilla Firefox",
                   "Ada <ada@example.com>", "2026-10-30T09:01:00+00:00")
        self._visited("The talk & the dates - sam@example.com - Gmail",
                      "https://mail.google.com/mail/u/1/#inbox/AbcdEfghIjklMnopQrstUvwxYz012345")
        item = handed.checked(EMAIL, lambda path: None)        # "Re: the talk & the date"
        acts = handed.actions(item, GMAIL, self.app.db)
        self.assertEqual({key: acts["reply"][key] for key in ("url", "found", "in")},
                         {"url": self.THREAD, "found": "thread", "in": "Gmail"},
                         "the one of their account, though another's was opened later")
        self.assertTrue(acts["open"].startswith("https://mail.google.com/mail/?"),
                        "the new message stays a button of its own")
        self.assertEqual(acts["copy"], EMAIL["body"])
        anyone = handed.actions(item, dict(GMAIL, account=""), self.app.db)["reply"]["url"]
        handed._threads.clear()
        self.assertIn("QgrcJHsBmKdLpWxTnRvZyCfGhJkLmNpQ",
                      handed.actions(item, dict(GMAIL, account=""), self.app.db)["reply"]["url"],
                      "no account known: the newest")
        self.assertTrue(anyone)

    def test_said_by_reply_to_and_in_other_languages(self) -> None:
        self._visited("回复：下周的组会 - sam@example.com - Gmail", self.THREAD)
        item = handed.checked(dict(EMAIL, subject="组会时间", reply_to={"subject": "下周的组会"}),
                              lambda path: None)
        self.assertEqual(handed.answers(item), "下周的组会")
        self.assertEqual(handed.actions(item, dict(GMAIL, mail="default"),
                                        self.app.db)["reply"]["found"], "thread")
        self.assertEqual(handed.unmarked("RE[2]: Fwd: AW: Plan"), "Plan")
        self.assertEqual(handed.unmarked("Rescheduled: talk"), "Rescheduled: talk")

    def test_not_found_a_search_in_gmail_else_a_new_message(self) -> None:
        item = handed.checked(EMAIL, lambda path: None)
        reply = handed.actions(item, GMAIL, self.app.db)["reply"]
        self.assertEqual(reply["found"], "search")
        self.assertTrue(reply["url"].startswith(
            "https://mail.google.com/mail/?authuser=sam@example.com#search/from%3Aada%40example.com"
            "%20subject%3A%28"))
        self.assertIn("the%20talk%20%26%20the%20date", reply["url"])
        outlook = handed.actions(item, dict(GMAIL, mail="outlook"), self.app.db)
        self.assertNotIn("reply", outlook, "opened as a new message, as before")
        plain = handed.checked(dict(EMAIL, subject="The talk"), lambda path: None)
        self.assertNotIn("reply", handed.actions(plain, GMAIL, self.app.db), "no reply")
        self.assertNotIn("reply", handed.actions(item, GMAIL), "no records given")

    def test_other_webmail_its_page_too(self) -> None:
        self._visited("The talk & the date - Yahoo Mail",
                      "https://mail.yahoo.com/d/folders/1/messages/12345")
        item = handed.checked(EMAIL, lambda path: None)
        reply = handed.actions(item, dict(GMAIL, mail="default", account=""), self.app.db)["reply"]
        self.assertEqual((reply["found"], reply["in"]), ("thread", "Yahoo Mail"))

    def test_checked_against_who_was_on_the_page(self) -> None:
        """The user, 2026-11-01: two emails may share a subject, and Reply
        answers whoever is in the thread opened."""
        other = "https://mail.google.com/mail/u/1/#inbox/QgrcJHsBmKdLpWxTnRvZyCfGhJkLmNpQ"
        title = "Meeting - sam@example.com - Gmail"
        self._visited(title, self.THREAD, at="2026-10-29T09:00:00+00:00")
        self._visited(title, other, at="2026-10-30T09:00:00+00:00")
        self._read(title + " — Mozilla Firefox", "Ada Lovelace <ada@example.com>\nto me\nSee you",
                   "2026-10-29T09:02:00+00:00")
        self._read(title + " — Mozilla Firefox", "Bo Quill <bo@example.com>\nThe room moved",
                   "2026-10-30T09:03:00+00:00")
        item = handed.checked(dict(EMAIL, subject="Re: Meeting", cc=[],
                                   reply_to={"from": "Ada Lovelace <ada@example.com>"}),
                              lambda path: None)
        reply = handed.actions(item, GMAIL, self.app.db)["reply"]
        self.assertEqual((reply["url"], reply["check"], reply["same_subject"]),
                         (self.THREAD, "matched", 2), "the older one, which Ada is on")
        self.assertEqual(reply["matched"], "ada@example.com")
        # Read, and no one it is for on it: never opened - a search instead.
        handed._threads.clear()
        nobody = handed.checked(dict(item, to=["cy@example.com"],
                                     reply_to={"from": "Cy <cy@example.com>"}), lambda p: None)
        reply = handed.actions(nobody, GMAIL, self.app.db)["reply"]
        self.assertEqual((reply["found"], reply["differs"]), ("search", True))
        # Kept: the people on it, never what it says.
        kept = {one["url"]: one for one in mail_pages.kept(self.app.db)}
        self.assertEqual(kept[self.THREAD]["people"], ["Ada Lovelace", "ada@example.com"])
        self.assertNotIn("See you", json.dumps(kept, ensure_ascii=False))

    def test_never_read_gmail_searches_and_elsewhere_the_only_one_is_opened(self) -> None:
        self._visited("Re: The talk & the date - sam@example.com - Gmail", self.THREAD)
        item = handed.checked(EMAIL, lambda p: None)
        self.assertEqual(handed.actions(item, GMAIL, self.app.db)["reply"]["found"], "search")
        handed._threads.clear()
        reply = handed.actions(item, dict(GMAIL, mail="default"), self.app.db)["reply"]
        self.assertEqual((reply["found"], reply["check"], reply["matched"]),
                         ("thread", "unchecked", ""))

    def _one_subject_many_threads(self):
        """One subject sent to several people, as the user's was (2026-11-04):
        two threads read without the one it is for, others never read."""
        base = "https://mail.google.com/mail/u/1/"
        title = "Meeting - sam@example.com - Gmail"
        self._visited(title, base + "#inbox/QgrcJHsBmKdLpWxTnRvZyCfGhJkLmNpQ", at="2026-10-29T09:00:00+00:00")
        self._read(title + " — Mozilla Firefox", "Bo Quill <bo@example.com>",
                   "2026-10-29T09:01:00+00:00")
        history = [(base + "#inbox/AbcdEfghIjklMnopQrstUvwxYz012345", "Gmail", title, "2026-10-30T09:00:00+00:00"),
                   (base + "#sent/QgrcJHsBmKdLpWxTnRvZyCfGhJkLmNpQ", "Gmail", title, "2026-10-31T09:00:00+00:00"),
                   (base + "#search/words/QgrcJHsBmKdLpWxTnRvZyCfGhJkLmNpQ", "Gmail", title, "2026-10-31T10:00:00+00:00")]
        item = handed.checked(dict(EMAIL, subject="Re: Meeting", cc=[], to=["cy@example.com"],
                                   reply_to={"from": "Cy <cy@example.com>"}), lambda p: None)
        return item, history

    def test_one_subject_many_threads_gmail_searches_for_who_it_answers(self) -> None:
        item, history = self._one_subject_many_threads()
        reply = handed.actions(item, GMAIL, self.app.db, lambda: history)["reply"]
        self.assertEqual(reply["found"], "search", "never the newest, on a guess")
        self.assertIn("from%3Acy%40example.com", reply["url"])
        self.assertEqual(reply["same_subject"], 2, "threads, not their pages")

    def test_one_subject_many_threads_elsewhere_a_new_message(self) -> None:
        item, history = self._one_subject_many_threads()
        said = handed.actions(item, dict(GMAIL, mail="default"), self.app.db, lambda: history)
        self.assertNotIn("reply", said)

    def test_a_thread_read_without_them_does_not_come_back_by_another_address(self) -> None:
        item, history = self._one_subject_many_threads()
        found = handed.thread_of(self.app.db, "Meeting", "", ["cy@example.com"],
                                 lambda: history[1:])
        self.assertEqual(found["check"], "differs", "#sent and #search of the same thread")

    def _a_name_on_the_page(self):
        title = "Meeting - sam@example.com - Gmail"
        self._visited(title, self.THREAD, at="2026-10-29T09:00:00+00:00")
        self._read(title + " — Mozilla Firefox", "Ben F. Hale <ben@other.example.com>",
                   "2026-10-29T09:01:00+00:00")
        return handed.checked(dict(EMAIL, subject="Re: Meeting", cc=[], to=["bj@example.com"],
                                   reply_to={"from": "Benjamin Hale <bj@example.com>"}),
                              lambda p: None)

    def test_whether_a_name_is_theirs_is_the_models(self) -> None:
        item = self._a_name_on_the_page()
        asked = []

        def judge(who, threads):
            asked.append((who, threads))
            return {thread: "yes" for thread in threads}
        reply = handed.actions(item, GMAIL, self.app.db, None, judge)["reply"]
        self.assertEqual((reply["found"], reply["url"], reply["check"]),
                         ("thread", self.THREAD, "matched"))
        self.assertEqual(asked[0][0], ["bj@example.com", "Benjamin Hale"])
        self.assertTrue(all("sam@example.com" not in people for people in asked[0][1].values()),
                        "their own address is no one to ask about")

    def test_asked_once_and_drawing_the_page_asks_nothing(self) -> None:
        item = self._a_name_on_the_page()
        self.assertEqual(handed.actions(item, GMAIL, self.app.db)["reply"]["found"], "search",
                         "not asked yet: the page says what is known")
        judge = mock.Mock(side_effect=lambda who, threads: {one: "yes" for one in threads})
        handed.actions(item, GMAIL, self.app.db, None, judge)
        handed._threads.clear()
        handed.actions(item, GMAIL, self.app.db, None, judge)
        self.assertEqual(judge.call_count, 1)
        handed._threads.clear()
        self.assertEqual(handed.actions(item, GMAIL, self.app.db)["reply"]["found"], "thread")

    def test_only_its_yes_opens_the_thread(self) -> None:
        item = self._a_name_on_the_page()
        for answer in ("no", "unsure"):
            handed._threads.clear()
            self.app.db.execute("DELETE FROM kv WHERE key LIKE 'reply.same_person.%'")
            reply = handed.actions(item, GMAIL, self.app.db, None,
                                   lambda who, threads: {one: answer for one in threads})["reply"]
            self.assertEqual(reply["found"], "search", answer)

    def test_a_model_that_does_not_answer_has_said_nothing(self) -> None:
        item = self._a_name_on_the_page()
        reply = handed.actions(item, GMAIL, self.app.db, None,
                               mock.Mock(side_effect=OSError("no model")))["reply"]
        self.assertEqual(reply["found"], "search")
        self.assertIsNone(self.app.db.query_one(
            "SELECT 1 FROM kv WHERE key LIKE 'reply.same_person.%'"), "asked again next time")

    def test_a_name_alone_is_never_code_s_to_match(self) -> None:
        self.assertEqual(handed._on_it(["Hale", "Ann Smith"], ["Benjamin Hale", "Ann"]), "")
        self.assertEqual(handed._on_it(["BJ@Example.com"], ["bj@example.com"]), "bj@example.com")

    def test_the_judge_reads_what_the_model_answers(self) -> None:
        models = mock.Mock()
        models.invoke.return_value = mock.Mock(text='{"T1": "YES", "T2": "no"}')
        said = handed.person_judge(models)(["bj@example.com"], {"a": ["Ben"], "b": ["Bo"], "c": ["Cy"]})
        self.assertEqual(said, {"a": "yes", "b": "no", "c": "unsure"})
        self.assertEqual(models.invoke.call_args.kwargs["role"], "fast_reply")

    def test_seen_with_them_on_one_page_is_theirs_on_all(self) -> None:
        item, history = self._one_subject_many_threads()
        found = handed.thread_of(self.app.db, "Meeting", "", ["bo@example.com"], lambda: history)
        self.assertEqual((found["check"], found["url"].rsplit("/", 1)[-1]),
                         ("matched", "QgrcJHsBmKdLpWxTnRvZyCfGhJkLmNpQ"))

    def test_kept_half_a_year_after_the_records_go(self) -> None:
        self._visited("Re: The talk & the date - sam@example.com - Gmail", self.THREAD)
        mail_pages.keep(self.app.db)
        self.app.db.execute("DELETE FROM raw_events")         # the records gone, after 8 days
        handed._threads.clear()
        reply = handed.actions(handed.checked(EMAIL, lambda p: None), dict(GMAIL, mail="default"),
                               self.app.db)["reply"]
        self.assertEqual(reply["url"], self.THREAD)
        self.app.db.execute("UPDATE work_mail_pages SET last_seen = '2025-01-01T00:00:00+00:00'")
        self.assertEqual(mail_pages.keep(self.app.db)["let_go"], 1, "not opened in 180 days")
        self.assertEqual(mail_pages.kept(self.app.db), [])

    def test_kept_before_the_records_are_let_go(self) -> None:
        self._visited("Re: The talk & the date - sam@example.com - Gmail", self.THREAD)
        said = self.app.housekeeping.run_once()
        self.assertEqual(said["kept_first"]["pages"], 1)
        self.assertEqual([one["url"] for one in mail_pages.kept(self.app.db)], [self.THREAD])

    def test_and_in_the_browsers_own_histories(self) -> None:
        """Months of them - copied into the agent's own folder, read, and the
        copy removed; who was on such a page is not known."""
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            places = Path(folder) / "places.sqlite"
            conn = sqlite3.connect(places)
            conn.execute("CREATE TABLE moz_places (url TEXT, title TEXT, last_visit_date INTEGER)")
            visited = int((time.time() - 60 * 86400) * 1_000_000)
            conn.executemany("INSERT INTO moz_places VALUES (?, ?, ?)", [
                (self.THREAD, "The talk & the date - sam@example.com - Gmail", visited),
                ("https://mail.google.com/mail/u/1/#inbox", "Inbox - sam@example.com - Gmail",
                 visited),
                ("https://example.com/news", "The talk & the date", visited)])
            conn.commit()
            conn.close()
            platform = mock.Mock()
            platform.activity.get_browser_history_paths.return_value = {"Firefox": str(places)}
            own = Path(folder) / "own"
            mail_pages._history.clear()
            self.addCleanup(mail_pages._history.clear)
            pages = mail_pages.from_history(platform, own)
            self.assertEqual([one[0] for one in pages], [self.THREAD], "message pages alone")
            self.assertEqual(list(own.glob("*")), [], "the copy removed")
            reply = handed.actions(handed.checked(EMAIL, lambda p: None),
                                   dict(GMAIL, mail="default"), self.app.db, lambda: pages)["reply"]
            self.assertEqual((reply["url"], reply["check"]), (self.THREAD, "unchecked"))

    def test_nothing_but_their_own_browser_opens_it(self) -> None:
        """The agent never sends: the button opens a page in the user's own
        browser and copies the reply - nothing reaches their account."""
        import inspect
        source = inspect.getsource(handed)
        for word in ("smtplib", "imaplib", "requests", "urlopen", "http.client"):
            self.assertNotIn(word, source)


class HowAndWhatToCheckTests(unittest.TestCase):
    """The user, 2026-11-01: with every result, how it is used and what to
    check - shown with it, and on the desktop when it is opened."""

    def test_kept_shown_and_asked_for(self) -> None:
        item = handed.checked(dict(EMAIL, check="- The date\n* The room\n\n"), lambda p: None)
        self.assertEqual((item["how"], item["check"]),
                         ("Open it, read it, and send it.", ["The date", "The room"]))
        self.assertIn("How: Open it, read it, and send it.", handed.shown(item))
        self.assertIn("- The room", handed.shown(item))
        self.assertEqual(handed.told_how(item), "")
        bare = {key: one for key, one in EMAIL.items() if key not in ("how", "check")}
        self.assertIn("how", handed.told_how(handed.checked(bare, lambda p: None)))

    def test_the_item_itself_on_the_left(self) -> None:
        item = handed.checked(EMAIL, lambda p: None)
        said = handed.note(item, copied=True)
        self.assertEqual(said["item"], {
            "kind": "email", "text": EMAIL["body"],
            "meta": ["Sent to: ada@example.com", "Copied to: bo@example.com, cy@example.com",
                     "Subject: Re: the talk & the date"], "editable": True})
        self.assertTrue(said["copied"])
        event = handed.what_it_is(handed.checked(
            {"kind": "event", "title": "Talk", "start": "2026-11-04", "where": "Room 2",
             "details": "Bring slides"}, lambda p: None))
        self.assertEqual((event["meta"], event["text"]),
                         (["When: 2026-11-04", "Where: Room 2"], "Bring slides"))

    def test_the_users_version_as_the_agent_is_told_it(self) -> None:
        mine = dict(handed.checked(EMAIL, lambda p: None), body="Dear Ada, Thursday?",
                    edited_at="2026-11-01T12:00:00+00:00")
        said = handed.users_version([handed.checked(EMAIL, lambda p: None), mine])
        self.assertTrue(said.startswith(handed.USERS_VERSION))
        self.assertIn("EMAIL - Reply to Ada (changed by the user 2026-11-01T12:00)", said)
        self.assertIn("<<<\nDear Ada, Thursday?\n>>>", said)
        self.assertNotIn("下周三可以吗", said, "only what the user changed")
        self.assertEqual(handed.users_version([handed.checked(EMAIL, lambda p: None)]), "")

    def test_a_file_has_no_words_to_change(self) -> None:
        self.assertFalse(handed.what_it_is({"kind": "file", "path": "C:/w/a.pdf"})["editable"])
        self.assertTrue(handed.what_it_is({"kind": "text", "text": "Sure."})["editable"])
        self.assertEqual(handed.WORDS_OF, {"email": "body", "event": "details", "text": "text",
                                           "answer": "text", "message": "text", "form": "parts"})

    def test_the_desktop_note(self) -> None:
        item = handed.checked(EMAIL, lambda p: None)
        said = handed.note(item, {"reply": {"found": "thread"}}, lambda text: "<" + text + ">")
        self.assertEqual(said["how"], "Open it, read it, and send it.")
        self.assertEqual(said["check"][0], "<It answers: the talk & the date>")
        self.assertIn("ada@example.com", said["check"][1])
        self.assertEqual(said["check"][-2], "The date: Wednesday the 4th")
        self.assertEqual(said["check"][-1], "<Does it sound like you - your words, your tone?>")
        self.assertEqual(handed.note(item)["check"], ["The date: Wednesday the 4th",
                                                      "Does it sound like you - your words, "
                                                      "your tone?"])

    def test_the_tool_refuses_one_without(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        tools = WorkTools.__new__(WorkTools)
        tools.on_hand_over = lambda item: self.fail("kept without how and check")
        tools.runtime = mock.Mock()
        tools.runtime.policy.write_root, tools.runtime.policy.extra_writable = ".", []
        bare = {key: one for key, one in EMAIL.items() if key not in ("how", "check")}
        self.assertIn("error", WorkTools.give_to_user(tools, bare))


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
                    # Words sent as the user's: how they write looked at first.
                    return inner._reply("", [ToolCall(id="v1", name="how_the_user_writes",
                                                      arguments={"where": "Email",
                                                                 "what": "a reply to Ada",
                                                                 "to": "Ada"})])
                if "on your own" in (request.system or "") and len(inner.quick) == 1 \
                        and not getattr(inner, "handed", False) and not isinstance(last, str):
                    # How they write, given back: the email handed over.
                    inner.handed = True
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
            said = routes[("POST", "/api/work/handed/open")](_Ask(run=run["id"], n=0,
                                                                   how="new"))
        self.assertEqual(said["opened"], "email")
        self.assertEqual(opened.call_args[0][0], acts["open"], "a new message, asked for")
        # Opened without saying how - the to-do's "Open the result": a reply
        # is opened where it answers, its words handed back to be copied -
        # and the note on the desktop says they are on the clipboard.
        notes = []
        self.app.bus.subscribe("work.handed_opened", lambda _topic, one: notes.append(one))
        order = []
        with mock.patch("webbrowser.open", side_effect=lambda url: order.append("opened")
                        or True) as opened, \
                mock.patch("personalized_agent.platform_layer.clipboard.copy",
                           side_effect=lambda text: order.append(("copied", text)) or True):
            said = routes[("POST", "/api/work/handed/open")](_Ask(run=run["id"], n=0))
        self.assertEqual(order, [("copied", EMAIL["body"]), "opened"],
                         "on the clipboard before the browser takes the front")
        self.assertTrue(said["copied"])
        self.assertTrue(notes[-1]["copied"])
        with mock.patch("webbrowser.open", return_value=True), \
                mock.patch("personalized_agent.platform_layer.clipboard.copy", return_value=False):
            said = routes[("POST", "/api/work/handed/open")](_Ask(run=run["id"], n=0))
        self.assertFalse(said["copied"])
        self.assertFalse(notes[-1]["copied"], "never said to be there when it is not")
        with mock.patch("webbrowser.open", return_value=True) as opened, \
                mock.patch("personalized_agent.platform_layer.clipboard.copy", return_value=True):
            said = routes[("POST", "/api/work/handed/open")](_Ask(run=run["id"], n=0))
        self.assertEqual(notes[-1]["item"]["text"], EMAIL["body"])
        self.assertEqual((notes[-1]["run"], notes[-1]["n"]), (run["id"], 0),
                         "the note knows what to save to")
        # ITS INSTRUCTIONS PANEL ALONE (the user, 2026-11-08): the note, nothing opened or copied.
        before = len(notes)
        with mock.patch("webbrowser.open") as browsed, \
                mock.patch("personalized_agent.platform_layer.clipboard.copy") as copied:
            alone = routes[("POST", "/api/work/handed/note")](_Ask(run=run["id"], n=0))
        self.assertEqual(alone["opened"], "note")
        browsed.assert_not_called()
        copied.assert_not_called()
        self.assertEqual(len(notes), before + 1, "the panel put up")
        self.assertEqual(notes[-1]["item"]["text"], EMAIL["body"])
        self.assertFalse(notes[-1]["copied"])
        # A WHOLE PROJECT'S, every item it made listed to open; one item's asked for, its own kind.
        with mock.patch.object(self.app.drafts, "handed_from_a_whole_project", return_value=True):
            routes[("POST", "/api/work/handed/note")](_Ask(run=run["id"], n=0))
            self.assertEqual(notes[-1]["scope"], "project")
            self.assertEqual([one["n"] for one in notes[-1]["items"]], [0])
            self.assertEqual(notes[-1]["items"][0]["kind"], "email")
            self.assertIn("project", notes[-1], "the project's part, on top")
            told = self.app.handed_told(run["id"], 0)
            self.assertEqual((told["run"], told["n"], told["scope"]), (run["id"], 0, "todo"))
            self.assertNotIn("project", told, "an item's own, for below")
            self.app.handed_note_of(run["id"], 0)
        self.assertEqual(notes[-1]["scope"], "todo", "its own panel, the project's left")
        self.assertNotIn("items", notes[-1])
        self.assertTrue(notes[-1]["item"]["editable"])
        # SAVE (the user, 2026-11-01): the words as the user changed them, kept
        # where the work keeps them - opened again, they are these.
        saved = self.app.work.save_handed(run["id"], 0, "Dear Ada,\n\nThursday instead?")
        self.assertEqual(saved["body"], "Dear Ada,\n\nThursday instead?")
        self.assertTrue(saved["edited_at"])
        again = routes[("GET", "/api/work/runs")](_Ask(unit=self.unit["id"]))["runs"][0]
        self.assertEqual(again["handed"][0]["body"], "Dear Ada,\n\nThursday instead?")
        self.assertEqual(again["handed"][0]["actions"]["copy"], "Dear Ada,\n\nThursday instead?")
        self.assertIsNone(self.app.work.save_handed(run["id"], 7, "x"), "no such item")
        self.assertIsNone(self.app.work.save_handed("run_nowhere", 0, "x"), "no such run")
        # Carried on, the work goes on from the user's version.
        from personalized_agent.agent.work.runner import Run
        told = Run._alone_brief(SimpleNamespace(
            plan={"handed": again["handed"]}, unit={"name": "Email to Ada"},
            _description_text=lambda: "", instruction="write Ada an email",
            _quick_learned=lambda: "", folder="C:/work", resumed=False))
        self.assertIn("THE USER CHANGED WHAT YOU HANDED OVER", told)
        self.assertIn("Dear Ada,\n\nThursday instead?", told)
        # Not while the work goes again: neither opened nor saved over it.
        class _Going:
            def alive(self):
                return True
        self.app.work._running[run["id"]] = _Going()
        self.assertEqual(self.app.work.save_handed(run["id"], 0, "x"), {"error": "going"})
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            routes[("POST", "/api/work/handed/open")](_Ask(run=run["id"], n=0))
        del self.app.work._running[run["id"]]
        self.app.work.save_handed(run["id"], 0, EMAIL["body"])     # as it was, for what follows
        self.assertEqual((said["opened"], said["copy"]), ("reply", EMAIL["body"]))
        self.assertEqual(opened.call_args[0][0], acts["reply"]["url"])
        # A reply ("Re: ..."): opened where it answers - here, a search for it.
        self.assertEqual(acts["reply"]["found"], "search")
        with mock.patch("webbrowser.open", return_value=True) as opened, \
                mock.patch("personalized_agent.platform_layer.clipboard.copy", return_value=True):
            said = routes[("POST", "/api/work/handed/open")](_Ask(run=run["id"], n=0,
                                                                   how="reply"))
        self.assertEqual(said, {"opened": "reply", "found": "search", "copy": EMAIL["body"],
                                "copied": True})
        self.assertEqual(opened.call_args[0][0], acts["reply"]["url"])
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
        spec = next(one for one in tools.specs() if one.name == "hand_over")
        self.assertLessEqual({"how", "check"}, set(spec.input_schema["required"]),
                             "how it is used and what to check, with every result")
        self.assertNotIn("hand_over", {one.name for one in tools.specs(writing=False)},
                         "not the reviewer's")
        self.assertIn("hand_over", {one.name for one in tools.specs()})


    def test_a_whole_projects_guide_kept_apart_and_told_only_to_such_a_work(self) -> None:
        """The user, 2026-11-08: a whole project's to-do tells the user, step by step, how to use
        each item's and what to do besides - as an item's does."""
        from types import SimpleNamespace as _NS
        from personalized_agent.agent.work.runner import Run
        from personalized_agent.agent.work.tools import WorkTools
        self.assertIn("error", handed.guide_of({"kind": "guide", "title": "Harbour"}))
        tools = WorkTools(self.app.work._runtime(self.unit, self.app.work.folder_of(self.unit)))
        tools.runs_code = ("run_python", "run_tests")
        got = []
        tools.on_hand_over = lambda item: got.append(item)
        said = tools.give_to_user({"kind": "guide", "title": "Harbour project",
                                   "how": "1. Send the email to Ada.\n2. Book the hall yourself.",
                                   "check": ["The hall is free on the 4th"]})
        self.assertEqual(said["handed"], "guide")
        self.assertEqual(got[0]["kind"], "guide")
        self.assertIn("Book the hall yourself", got[0]["how"])
        # Kept in the run's plan - not among what it made.
        events = []
        run = _NS(plan={"handed": [{"kind": "email", "title": "Re: The talk"}]},
                  _event=lambda *a: events.append(a), owner=_NS(save=lambda one: None))
        self.assertIsNone(Run._hand_over(run, got[0]))
        self.assertEqual(run.plan["guide"]["how"], got[0]["how"])
        self.assertEqual([one["kind"] for one in run.plan["handed"]], ["email"])
        # Told only to a whole project's work.
        self.app.db.execute("INSERT INTO work_drafts (id, unit_id, source, options, created_at, "
                            "updated_at) VALUES ('d1', ?, 'project', ?, '', '')",
                            ("task-whole", json.dumps({"whole": True})))
        whole = _NS(owner=_NS(db=self.app.db), unit={"id": "task-whole"})
        self.assertTrue(Run._a_whole_project(whole))
        self.assertFalse(Run._a_whole_project(_NS(owner=_NS(db=self.app.db), unit={"id": "task-x"})))
        self.assertIn("ONE GUIDE", handed.PROJECT_GUIDE_RULES)


if __name__ == "__main__":
    unittest.main()

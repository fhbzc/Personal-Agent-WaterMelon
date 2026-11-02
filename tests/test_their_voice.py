"""IN THE USER'S OWN VOICE (the user, 2026-11-01: "这个有学我的语气吗" - in a
group, on Slack, on Facebook too): before words the user sends or posts as
their own are handed over, the work looks at how they write - to that
person, in that chat or group, else in general - and hand_over holds them
till it has; on the desktop, the last thing to check is that it sounds like
them. Everyone and everything here is made up."""

from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from personalized_agent.agent.work import handed
from personalized_agent.agent.work.tools import WorkTools, same_chat, voice_register


class _Records:
    """The user's records, as the work reads them: a chat seen in three
    pieces, another chat, and words kept of a contact's."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE listener_wechat_conversations (id TEXT, peer_name TEXT);
            CREATE TABLE listener_wechat_messages (id TEXT, conversation_id TEXT,
                sender_role TEXT, text TEXT, message_time TEXT, first_seen_at TEXT);
            CREATE TABLE connection_writing (at TEXT, channel TEXT, text TEXT);
            INSERT INTO listener_wechat_conversations VALUES
                ('c1', 'garden builders'), ('c2', 'garden builders'), ('c3', 'garden builders'),
                ('c4', 'garden 用户服务群'), ('c5', 'Mira');
            INSERT INTO listener_wechat_messages VALUES
                ('m1', 'c1', 'self', '新版好了，大家试试', '2026-10-27T10:00', NULL),
                ('m2', 'c2', 'other', '收到', '2026-10-28T10:00', NULL),
                ('m3', 'c3', 'self', 'bug修了，重启一下就行', '2026-10-29T10:00', NULL),
                ('m4', 'c4', 'self', '欢迎各位使用，有问题群里说', '2026-10-30T10:00', NULL),
                ('m5', 'c5', 'self', 'ok 明天见哈', '2026-10-31T10:00', NULL);
            INSERT INTO connection_writing VALUES ('2026-10-20T09:00', 'screen', 'Thanks, will do!');
        """)

    def query(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]


def _tools(records=None, people=None, handed_to=None, files=None):
    tools = WorkTools.__new__(WorkTools)
    tools.records_db, tools.people, tools._voice_looked = records, people, []
    tools.files_index = files
    tools.on_hand_over = (handed_to.append if handed_to is not None
                          else lambda item: None)
    tools.runtime = mock.Mock()
    tools.runtime.policy.write_root, tools.runtime.policy.extra_writable = ".", []
    return tools


MESSAGE = {"kind": "message", "title": "Update for the builders", "app": "WeChat",
           "to": ["garden builders"], "text": "新版好了", "how": "Paste it and send it.",
           "check": ["The version number"]}


class WhichChatTests(unittest.TestCase):

    def test_a_chat_by_the_words_its_name_is_asked_by(self) -> None:
        self.assertTrue(same_chat("personal agent developing group", "personalized agent"))
        self.assertTrue(same_chat("garden builders (group)", "garden builders"))
        self.assertFalse(same_chat("garden builders", "garden 用户服务群"),
                         "another group of a like name")
        self.assertFalse(same_chat("Ada", "Bob"))
        self.assertFalse(same_chat("", "Bob"))


class _Files:
    """The user's own files, as the local index finds them by words."""

    def __init__(self, found):
        self.found, self.asked = found, []

    def search(self, query, allowed=None, limit=8):
        self.asked.append(query)
        return self.found


REVIEW = [{"file": "C:/w/reviews/graph_paper_review.md", "modified": "2026-09-02",
           "text": "Summary. The paper proposes ... Strengths: ... Weaknesses: ..."}]


class HowTheyWriteTests(unittest.TestCase):
    """The user, 2026-11-01: how they write depends on where it goes and on
    what it says."""

    def test_where_it_goes_sets_the_register(self) -> None:
        self.assertEqual([voice_register(one) for one in
                          ("WeChat", "Slack", "Facebook", "微信", "Email", "Gmail",
                           "a journal's review form", "a document")],
                         ["chat"] * 4 + ["email"] * 2 + ["formal"] * 2)

    def test_where_and_what_both_asked(self) -> None:
        tools = _tools(_Records())
        self.assertIn("error", tools.how_the_user_writes({"where": "WeChat"}))
        self.assertIn("error", tools.how_the_user_writes({"what": "an update"}))
        self.assertIn("error", _tools().how_the_user_writes({"where": "WeChat", "what": "x"}))

    def test_their_own_lines_in_that_chat_however_many_pieces_it_was_seen_in(self) -> None:
        said = _tools(_Records()).how_the_user_writes({
            "where": "WeChat", "what": "an update on the new version",
            "to": "garden builders group"})
        self.assertEqual([one["text"] for one in said["there"]],
                         ["新版好了，大家试试", "bug修了，重启一下就行"],
                         "theirs alone, the latest last - none of another chat")
        self.assertEqual(said["chats"], ["garden builders"])
        self.assertIn("elsewhere", said, "a chat, few there: how they chat in general too")
        self.assertNotIn("收到", [one["text"] for one in said["elsewhere"]])

    def test_slack_or_facebook_with_nothing_kept_there_how_they_chat(self) -> None:
        said = _tools(_Records()).how_the_user_writes({
            "where": "Slack", "what": "a reply about the release date", "to": "#garden-dev"})
        self.assertEqual(said["there"], [])
        texts = [one["text"] for one in said["elsewhere"]]
        self.assertIn("ok 明天见哈", texts)
        self.assertIn("Thanks, will do!", texts)

    def test_a_formal_piece_has_no_chat_for_a_model_but_their_like_pieces(self) -> None:
        files = _Files(REVIEW)
        said = _tools(_Records(), files=files).how_the_user_writes({
            "where": "a journal's review form", "what": "a review of a paper on graph learning"})
        self.assertEqual(said["register"], "formal")
        self.assertNotIn("elsewhere", said, "no chat lines for a review")
        self.assertEqual(files.asked, ["a review of a paper on graph learning"],
                         "found by what it says")
        self.assertEqual(said["like_this"][0]["file"], REVIEW[0]["file"])
        self.assertIn("no model for it", said["said"])

    def test_an_email_has_no_chat_for_a_model(self) -> None:
        said = _tools(_Records()).how_the_user_writes({
            "where": "Email", "what": "asking to move the talk", "to": "garden builders"})
        self.assertNotIn("elsewhere", said)
        self.assertEqual(said["there"], [], "a WeChat group's lines are not an email's")

    def test_a_contacts_kept_words_from_any_app(self) -> None:
        people = mock.Mock()
        people.search.return_value = [{"id": "p1", "name": "Mira Holt"}]
        people.writing.return_value = [{"at": "2026-10-01T08:00", "channel": "Facebook",
                                        "text": "Haha see you Sat"}]
        said = _tools(None, people).how_the_user_writes({
            "where": "Messenger", "what": "plans for Saturday", "to": "Mira"})
        self.assertEqual(said["contact"], "Mira Holt")
        self.assertEqual(said["there"][0]["text"], "Haha see you Sat")


class HeldTillLookedTests(unittest.TestCase):

    def test_words_sent_as_theirs_wait_for_a_look_at_how_they_write(self) -> None:
        kept = []
        tools = _tools(_Records(), handed_to=kept)
        said = tools.give_to_user(dict(MESSAGE))
        self.assertIn("how_the_user_writes", said["error"])
        self.assertEqual(kept, [])
        tools.how_the_user_writes({"where": "WeChat", "what": "an update",
                                   "to": "garden builders"})
        self.assertEqual(tools.give_to_user(dict(MESSAGE))["handed"], "message")
        self.assertEqual(len(kept), 1)

    def test_a_file_or_an_answer_is_not_held(self) -> None:
        kept = []
        tools = _tools(_Records(), handed_to=kept)
        said = tools.give_to_user({"kind": "answer", "title": "The sum", "text": "42",
                                   "how": "Read it.", "check": ["The sum"]})
        self.assertEqual(said["handed"], "answer")

    def test_nothing_to_tell_from_nothing_held(self) -> None:
        self.assertEqual(_tools().give_to_user(dict(MESSAGE))["handed"], "message")


class CheckedOnTheDesktopTests(unittest.TestCase):

    def test_the_last_thing_to_check_is_that_it_sounds_like_them(self) -> None:
        item = handed.checked(dict(MESSAGE), lambda p: None)
        said = handed.note(item, None, lambda text: "<" + text + ">")
        self.assertEqual(said["check"][-1], "<Does it sound like you - your words, your tone?>")
        answer = handed.checked({"kind": "answer", "title": "T", "text": "42", "how": "h",
                                 "check": ["c"]}, lambda p: None)
        self.assertEqual(handed.note(answer)["check"], ["c"])


if __name__ == "__main__":
    unittest.main()

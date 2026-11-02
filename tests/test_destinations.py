"""WHERE WHAT IS HANDED OVER IS USED (the user, 2026-11-01): a message opened
where the person is talked with - the chat, or the app - and copied, never
sent, nothing clicked for the user; a form's page opened in their own
browser, each part to paste; and the work told what the user uses, to hand
things over in it. Everyone and everything here is made up."""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.work import destinations as dest
from personalized_agent.agent.work import handed

MESSAGE = {"kind": "message", "title": "Thank Mei", "to": "Wang Mei", "app": "WeChat",
           "text": "Thank you for the slides!", "how": "Paste it and send it.",
           "check": ["Her name"]}
FORM = {"kind": "form", "title": "Review of the fictional paper",
        "url": "https://review.example.org/paper/7",
        "parts": [{"field": "Summary", "text": "The paper studies ferns."},
                  {"field": "Weaknesses", "text": "The sample is small."}],
        "how": "Paste each part into its box.", "check": ["The score"]}


class TheAppsTests(unittest.TestCase):

    def test_each_app_opens_as_it_can(self) -> None:
        self.assertEqual(dest.chat_of("Weixin")["key"], "wechat")
        self.assertEqual(dest.chat_of("MS Teams")["key"], "teams")
        self.assertEqual(dest.chat_of("iMessage")["key"], "sms")
        self.assertEqual(dest.chat_of("Pigeon post"), {})
        wa = dest.chat_link("WhatsApp", "+1 (555) 010-2030", "Hi & bye")
        self.assertEqual((wa["how"], wa["fills"]), ("link", True))
        self.assertEqual(wa["url"], "https://wa.me/15550102030?text=Hi%20%26%20bye")
        teams = dest.chat_link("Teams", "ada@example.com", "Hi")
        self.assertEqual(teams["url"],
                         "https://teams.microsoft.com/l/chat/0/0?users=ada@example.com&message=Hi")
        self.assertEqual(dest.chat_link("Telegram", "ada_l", "Hi")["url"], "https://t.me/ada_l")
        self.assertEqual(dest.chat_link("Slack", "", "Hi")["how"], "app")
        self.assertEqual(dest.chat_link("WeChat", "", "Hi")["how"], "wechat")
        self.assertEqual(dest.chat_link("WhatsApp", "", "Hi")["how"], "copy",
                         "no number: copied, the chat to be found")
        self.assertEqual(dest.chat_link("Pigeon post", "", "Hi"),
                         {"name": "Pigeon post", "how": "copy", "url": "", "fills": False})

    def test_what_the_user_uses(self) -> None:
        dest._uses.clear()
        self.addCleanup(dest._uses.clear)
        rows = ([{"application": "Weixin.exe", "data": "{}"}] * 9
                + [{"application": "slack.exe", "data": "{}"}] * 4
                + [{"application": "POWERPNT.EXE", "data": "{}"}] * 3
                + [{"application": "Firefox", "data": json.dumps(
                    {"url": "https://docs.google.com/document/d/x"})}] * 5
                + [{"application": "Zoom.exe", "data": "{}"}] * 2)       # too few
        db = SimpleNamespace(query=lambda sql, params=(): rows)
        said = dest.uses(db)
        self.assertEqual(said["chats"], ["wechat", "slack"])
        self.assertEqual(said["documents"], ["Google Docs"])
        self.assertEqual(said["slides"], ["Microsoft PowerPoint (.pptx)"])
        self.assertNotIn("meetings", said)
        text = dest.uses_text(db, "Gmail", "Google Calendar")
        for words in ("mail: Gmail", "calendar: Google Calendar", "chats: WeChat, Slack",
                      "documents: Google Docs", "browser: Firefox"):
            self.assertIn(words, text)


class TheKindsTests(unittest.TestCase):

    def test_a_message_and_a_form_kept_shown_and_offered(self) -> None:
        message = handed.checked(MESSAGE, lambda p: None)
        self.assertEqual((message["to"], message["app"]), ("Wang Mei", "WeChat"))
        self.assertIn("MESSAGE - Thank Mei\nTo: Wang Mei in WeChat", handed.shown(message))
        acts = handed.actions(message, {"mail": "gmail", "calendar": "google"})
        self.assertEqual((acts["in"], acts["chat"]["how"], acts["copy"]),
                         ("WeChat", "wechat", "Thank you for the slides!"))
        self.assertIn("error", handed.checked(dict(MESSAGE, to=""), lambda p: None))
        form = handed.checked(FORM, lambda p: None)
        self.assertEqual([one["field"] for one in form["parts"]], ["Summary", "Weaknesses"])
        acts = handed.actions(form, {"mail": "gmail", "calendar": "google"})
        self.assertEqual((acts["open"], acts["copy"], acts["parts"]),
                         ("https://review.example.org/paper/7", "The paper studies ferns.", 2))
        self.assertIn("[Weaknesses]\nThe sample is small.", handed.shown(form))
        self.assertIn("error", handed.checked(dict(FORM, url="ftp://x"), lambda p: None))
        self.assertIn("error", handed.checked(dict(FORM, parts=[]), lambda p: None))
        left = handed.what_it_is(form)
        self.assertEqual((left["parts"][1]["text"], left["editable"]),
                         ("The sample is small.", True))
        self.assertIn("WHICH APP - and whether an email or a message at\n    all - IS YOURS TO "
                      "CHOOSE", handed.HAND_OVER_RULES)


class OpenedForTheUserTests(unittest.TestCase):
    """Opened through the page: copied first, then the place opened - nothing
    clicked or sent for the user."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-msg1", "name": "Thank Mei", "kind": "task", "why": ""}
        self.app.db.execute(
            "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, plan, "
            "log, final, started_at, ended_at, updated_at) VALUES ('run_m', ?, 'Thank Mei', '', "
            "'done', 'thank her', ?, '[]', '', '2026-11-01T09:00:00', '2026-11-01T09:10:00', "
            "'2026-11-01T09:10:00')",
            (self.unit["id"], json.dumps({"handed": [
                handed.checked(MESSAGE, lambda p: None),
                handed.checked(dict(MESSAGE, app="WhatsApp", handle="+15550102030"),
                               lambda p: None),
                handed.checked(FORM, lambda p: None)]})))
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)
        self.notes = []
        self.app.bus.subscribe("work.handed_opened", lambda _t, one: self.notes.append(one))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _open(self, n):
        class _Ask(dict):
            def get(self, key, default=None):
                return super().get(key, default)

            def require(self, key):
                return self[key]

            def int_param(self, key, default=0):
                return int(self.get(key, default))
        order = []
        with mock.patch("personalized_agent.platform_layer.clipboard.copy",
                        side_effect=lambda text: order.append(("copied", text)) or True), \
                mock.patch("webbrowser.open", side_effect=lambda url: order.append(url) or True), \
                mock.patch("personalized_agent.platform_layer.wechat_open.bring_forward",
                           side_effect=lambda: order.append("wechat forward") or
                           {"opened": True}):
            said = self.routes[("POST", "/api/work/handed/open")](_Ask(run="run_m", n=n))
        return said, order

    def test_wechat_brought_forward_the_rest_said(self) -> None:
        said, order = self._open(0)
        self.assertEqual(order, [("copied", "Thank you for the slides!"), "wechat forward"],
                         "copied, then WeChat in front - nothing else done in it")
        self.assertEqual((said["opened"], said["how"], said["copied"]), ("message", "wechat", True))
        self.assertEqual(self.notes[-1]["check"][0], "In WeChat, search Wang Mei (Ctrl+F), open "
                         "the chat, paste it (Ctrl+V) and send it yourself")
        self.assertTrue(self.notes[-1]["copied"])

    def test_a_link_opens_the_chat_with_the_message_in_it(self) -> None:
        said, order = self._open(1)
        self.assertEqual(order[1], "https://wa.me/15550102030?text=Thank%20you%20for%20the%20slides%21")
        self.assertEqual(self.notes[-1]["check"][0],
                         "The message is filled in, in WhatsApp - read it, then send it yourself")

    def test_a_forms_page_opened_its_first_part_copied(self) -> None:
        said, order = self._open(2)
        self.assertEqual(order, [("copied", "The paper studies ferns."),
                                 "https://review.example.org/paper/7"])
        self.assertTrue(said["copied"])
        self.assertIn("starting with Summary", self.notes[-1]["check"][0])
        self.assertEqual([one["field"] for one in self.notes[-1]["item"]["parts"]],
                         ["Summary", "Weaknesses"])
        saved = self.app.work.save_handed("run_m", 2, ["Ferns, studied well.", "Small sample."])
        self.assertEqual([one["text"] for one in saved["parts"]],
                         ["Ferns, studied well.", "Small sample."])
        self.assertIn("[Summary]\nFerns, studied well.", handed.users_version([saved]))

    def test_a_handle_from_the_contacts(self) -> None:
        store = self.app.connections
        mei = store.add("Wang Mei", relationship="classmate")
        store.add_account(mei["id"], platform="WhatsApp", handle="+15550109999")
        self.assertEqual(dest.handle_for(store, "whatsapp", "Wang Mei"), "+15550109999")
        self.assertEqual(dest.handle_for(store, "telegram", "Wang Mei"), "")
        self.assertEqual(dest.handle_for(store, "whatsapp", "Nobody Known"), "")


if __name__ == "__main__":
    unittest.main()

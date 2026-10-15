"""Passwords and codes never kept as they read (common/credentials_guard.py):
cut out by keywords and shapes before anything is recorded, a screenshot
showing one not kept, a picture showing one not given to a model - and no
model asked, ever."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from PIL import Image

from support import TemporaryAgent
from test_screenshots import _Base, slide

from personalized_agent.common.credentials_guard import HIDDEN, hide_credentials
from personalized_agent.common.events.models import ListenerEvent


class WhatIsHiddenTests(unittest.TestCase):

    def test_a_labelled_password_keeps_its_label(self) -> None:
        self.assertEqual(hide_credentials("Password: Orchid-7741"), f"Password: {HIDDEN}")
        self.assertEqual(hide_credentials("user = ana.example and pwd=Lantern99"),
                         f"user = ana.example and pwd={HIDDEN}")

    def test_the_account_beside_it_is_kept(self) -> None:
        # Whose account it was is what makes what was done readable.
        self.assertIs(hide_credentials("username: ana.example"), "username: ana.example")
        self.assertEqual(hide_credentials("账号：ana_example 密码：qwerty123"),
                         f"账号：ana_example 密码：{HIDDEN}")

    def test_a_one_time_code(self) -> None:
        self.assertEqual(hide_credentials("Your verification code is 482913. It expires."),
                         f"Your verification code is {HIDDEN} It expires.")
        self.assertEqual(hide_credentials("验证码为 739204，请勿泄露"), f"验证码为 {HIDDEN}，请勿泄露")

    def test_every_language_the_screens_are_shown_in(self) -> None:
        # English and Chinese above; Spanish, Japanese, Korean, traditional
        # Chinese - and only the value, not the ending after it.
        for text, said in (
                ("Tu código es 482913, válido", f"Tu código es {HIDDEN}, válido"),
                ("Contraseña: Girasol-55", f"Contraseña: {HIDDEN}"),

                ("認証コードは482913です", f"認証コードは{HIDDEN}です"),
                ("パスワード：Sakura-2024", f"パスワード：{HIDDEN}"),
                ("인증번호는 482913입니다", f"인증번호는 {HIDDEN}입니다"),

                ("驗證碼為 739204", f"驗證碼為 {HIDDEN}"),
                ("密碼：Lantern99", f"密碼：{HIDDEN}")):
            self.assertEqual(hide_credentials(text), said, text)
        for text in ("La cuenta de resultados del trimestre", "Hoy es lunes",
                     "usuario: ana.ejemplo", "아이디: ana_example", "帳號：ana_example"):
            self.assertIs(hide_credentials(text), text)

    def test_a_sign_in_form_read_off_the_screen(self) -> None:
        said = hide_credentials("登录\n账号\nana_example\n密码\nqwerty123")
        self.assertIn("ana_example", said)
        self.assertNotIn("qwerty123", said)

    def test_addresses(self) -> None:
        self.assertEqual(hide_credentials("https://ana:s3cret@git.example.org/r.git"),
                         f"https://ana:{HIDDEN}@git.example.org/r.git")
        self.assertEqual(hide_credentials("https://example.org/reset?token=abcDEF123456&lang=en"),
                         f"https://example.org/reset?token={HIDDEN}&lang=en")

    def test_what_keys_look_like(self) -> None:
        for key in ("sk-abcdefghijklmnopqrstuvwxyz123456", "AKIAABCDEFGHIJKLMNOP",
                    "ghp_abcdefghijklmnopqrstuvwxyz0123",
                    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJlLXZhbHVl"):
            self.assertNotIn(key, hide_credentials(f"the value {key} here"), key)
        block = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
        self.assertEqual(hide_credentials(block + "\nafter"), f"{HIDDEN}\nafter")

    def test_ordinary_words_are_left_alone(self) -> None:
        for text in ("Reading about password managers in a blog post",
                     "The meeting is at 3pm in room 204", "account balance overview"):
            self.assertIs(hide_credentials(text), text)

    def test_it_walks_what_an_event_holds(self) -> None:
        data = {"title": "Sign in", "lines": ["Password: Orchid-7741"], "n": 3}
        said = hide_credentials(data)
        self.assertEqual(said["lines"], [f"Password: {HIDDEN}"])
        self.assertEqual(said["n"], 3)
        same = {"title": "Plain"}
        self.assertIs(hide_credentials(same), same)


class BeforeAnythingIsKeptTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("listener_enabled", True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _kept(self, **data) -> str:
        event = ListenerEvent(event_type="screen.text", source="a-window-reader",
                              application="Notes", data=data)
        self.app.listener._on_event(event)  # noqa: SLF001 - the pipeline itself
        kept = [e for e in self.app.raw_store.recent_events(limit=50) if e.id == event.id]
        self.assertEqual(len(kept), 1)
        return json.dumps(kept[0].to_dict(), default=str, ensure_ascii=False)

    def test_on_by_default_and_hidden_before_it_is_kept(self) -> None:
        self.assertTrue(self.app.config.get("listener_hide_credentials"))
        stored = self._kept(text="Wi-Fi password: Orchid-7741 for the guest room")
        self.assertNotIn("Orchid-7741", stored)
        self.assertIn("guest room", stored)

    def test_turned_off_it_is_kept_as_it_reads(self) -> None:
        self.app.config.set("listener_hide_credentials", False)
        self.assertIn("Orchid-7741", self._kept(text="password: Orchid-7741"))

    def test_the_agents_own_sign_in_ticket_goes_either_way(self) -> None:
        from personalized_agent.common.own_interface import TICKET_PARAM
        self.app.config.set("listener_hide_credentials", False)
        url = self.app.sign_in_url("/#overview")
        ticket = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)[TICKET_PARAM][0]
        self.assertNotIn(ticket, self._kept(url=url))


class AScreenshotShowingOneTests(_Base):

    def setUp(self) -> None:
        super().setUp()
        from personalized_agent.runtime import documents
        self.documents = documents
        self.read = documents.ocr_picture
        documents.ocr_picture = lambda picture: "Sign in\nPassword: Orchid-7741"

    def tearDown(self) -> None:
        self.documents.ocr_picture = self.read

    def test_is_not_kept(self) -> None:
        self.assertEqual(self._show("POWERPNT.EXE", "deck.pptx - PowerPoint", slide()), [])
        self.assertEqual(self.events, [])
        self.assertEqual(list(self.folder.rglob("*.jpg")), [])
        self.assertEqual(self.provider.get_status()["held_back_for_credentials"], 1)

    def test_turned_off_it_is(self) -> None:
        self.config["listener_hide_credentials"] = False
        self.assertEqual(len(self._show("POWERPNT.EXE", "deck.pptx - PowerPoint", slide())), 1)

    def test_one_without_is(self) -> None:
        self.documents.ocr_picture = lambda picture: "Quarterly results"
        self.assertEqual(len(self._show("POWERPNT.EXE", "deck.pptx - PowerPoint", slide())), 1)


class APictureShowingOneTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.runtime import documents
        self.documents = documents
        self.work = Path(tempfile.mkdtemp())
        documents.configure(self.work / "extractions", hiding=lambda: True)
        self.read = documents.ocr_file
        documents.ocr_file = lambda path: "API key: sk-abcdefghijklmnopqrstuvwxyz123456"
        self.picture = self.work / "note.png"
        slide().save(self.picture)

    def tearDown(self) -> None:
        self.documents.ocr_file = self.read
        self.documents.configure(None, hiding=lambda: True)

    def test_its_words_are_hidden_and_the_picture_not_given(self) -> None:
        said = self.documents.read_for_model(self.picture)
        self.assertEqual(said["text"], f"API key: {HIDDEN}")
        self.assertNotIn("_pictures", said)
        self.assertIn("a password or a code", said["picture_held_back"])

    def test_a_picture_without_one_is_given(self) -> None:
        self.documents.ocr_file = lambda path: "A chart of the quarter"
        Image.new("RGB", (40, 40), "red").save(self.work / "plain.png")
        said = self.documents.read_for_model(self.work / "plain.png")
        self.assertIn("_pictures", said)


if __name__ == "__main__":
    unittest.main()

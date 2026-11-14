"""FILES AND PICTURES ATTACHED IN CHAT (the user, 2026-11-04: "要和gpt一样支持上传附件或者
图片"). Everything here is made up."""

from __future__ import annotations

import base64
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent, keyed
from test_agent_and_api import _free_port

from personalized_agent.agent import chat_files
from personalized_agent.agent.chat_files import ChatFiles


def _png(size=(4, 4)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


class StoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self.held = tempfile.TemporaryDirectory()
        self.files = ChatFiles(Path(self.held.name) / "chat_files")

    def tearDown(self) -> None:
        self.held.cleanup()

    def test_kept_in_its_own_folder_whatever_it_is_called(self) -> None:
        one = self.files.save("../../outside/notes.txt", b"plant in spring")
        self.assertEqual(one["name"], "notes.txt")
        self.assertFalse(one["picture"])
        self.assertTrue((self.files.folder / one["id"] / "notes.txt").is_file())
        self.assertEqual(self.files.file(one["id"])[0], b"plant in spring")

    def test_an_id_reaches_nothing_else(self) -> None:
        for ident in ("../secret", "chatfile_../x", "", "notes.txt", "chatfile_nothing"):
            with self.assertRaises(KeyError):
                self.files.file(ident)

    def test_too_large_or_empty(self) -> None:
        with self.assertRaises(ValueError):
            self.files.save("a.txt", b"")
        with self.assertRaises(ValueError):
            self.files.save("a.txt", b"x" * (chat_files.MOST_BYTES + 1))

    def test_its_words_with_the_message(self) -> None:
        one = self.files.save("notes.txt", b"plant in spring")
        said = self.files.for_the_model([one["id"]])
        self.assertIn(f"--- attached: notes.txt (id {one['id']}) ---\nplant in spring", said["text"])
        self.assertEqual(said["pictures"], [])
        self.assertEqual(said["files"][0]["id"], one["id"])

    def test_long_words_are_cut_and_said_to_be(self) -> None:
        one = self.files.save("long.txt", b"y" * (chat_files.MOST_WORDS + 50))
        self.assertIn("cut here", self.files.for_the_model([one["id"]])["text"])

    def test_a_picture_goes_as_a_picture(self) -> None:
        one = self.files.save("bed.png", _png())
        self.assertTrue(one["picture"])
        said = self.files.for_the_model([one["id"]])
        self.assertEqual(said["pictures"][0]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(said["pictures"][0]["data"]), _png())

    def test_a_large_picture_is_made_smaller(self) -> None:
        one = self.files.save("bed.png", _png((300, 200)))
        held = chat_files.MOST_PICTURE_BYTES
        chat_files.MOST_PICTURE_BYTES = 10
        try:
            picture = self.files.for_the_model([one["id"]])["pictures"][0]
        finally:
            chat_files.MOST_PICTURE_BYTES = held
        self.assertEqual(picture["media_type"], "image/jpeg")

    def test_too_many_or_gone(self) -> None:
        with self.assertRaises(ValueError):
            self.files.for_the_model(["x"] * (chat_files.MOST_FILES + 1))
        with self.assertRaises(ValueError):
            self.files.for_the_model(["chatfile_gone"])

    def test_later_turns_have_its_name_id_and_start(self) -> None:
        one = self.files.save("notes.txt", b"plant in spring")
        message = {"content": "Read this", "metadata": {"attachments": [one]}}
        said = self.files.later([message])[0]["content"]
        self.assertIn(f"[attached: notes.txt, id {one['id']}]", said)
        self.assertIn("plant in spring", said)
        self.assertEqual(message["content"], "Read this", "what is kept is not changed")
        plain = {"content": "Hello", "metadata": {}}
        self.assertIs(self.files.later([plain])[0], plain)

    def test_the_latest_pictures_go_on_being_sent(self) -> None:
        kept = [{"content": f"Bed {n}", "metadata": {"attachments": [
            self.files.save(f"bed{n}.png", _png())]}} for n in range(chat_files.MOST_PICTURES_LATER + 2)]
        said = self.files.later(kept)
        self.assertEqual([len(one["pictures"]) for one in said],
                         [0, 0] + [1] * chat_files.MOST_PICTURES_LATER, "the latest ones")
        self.assertIn("id chatfile_", said[0]["content"], "an older one can still be asked for")

    def test_a_file_since_gone_does_not_stop_the_chat(self) -> None:
        message = {"content": "Read", "metadata": {"attachments": [
            {"id": "chatfile_gone", "name": "old.txt", "picture": False}]}}
        self.assertIn("no longer there", self.files.later([message])[0]["content"])

    def test_read_on_from_where_it_was_cut(self) -> None:
        one = self.files.save("long.txt", ("a" * chat_files.MOST_WORDS + "the end").encode())
        said = self.files.read(one["id"], chat_files.MOST_WORDS)
        self.assertEqual(said["text"], "the end")
        self.assertEqual(said["of"], chat_files.MOST_WORDS + 7)

    def test_an_older_picture_looked_at_again(self) -> None:
        one = self.files.save("bed.png", _png())
        self.assertEqual(self.files.read(one["id"])["_pictures"][0]["media_type"], "image/png")


class TwoSpeedsTests(unittest.TestCase):
    """THE USER, 2026-11-04: a quick reply at once, the full agent folded under it."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.two = self.app.two_speed
        self.asked = []
        self.answers = []
        self.app.agent.models = self._Models(self)
        self.two.models = self.app.agent.models

    class _Models:
        def __init__(self, test):
            self.test, self.real = test, test.app.agent.models

        def __getattr__(self, name):
            return getattr(self.real, name)

        def invoke(self, request, role="primary"):
            self.test.asked.append((role, request))
            text = self.test.quick if role == "fast_reply" else self.test.full
            if isinstance(text, Exception):
                raise text
            return SimpleNamespace(text=text, tool_calls=[], usage=None, stop_reason="end_turn",
                                   content=[{"type": "text", "text": text}])

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _done(self, turn):
        for _ in range(300):
            state = self.two.state(turn)
            if state["state"] != "running":
                return state
            time.sleep(0.02)
        self.fail("the full agent never finished")

    def _messages(self, conversation):
        return self.app.conversations.messages(conversation, limit=None)

    def test_stopped_only_the_quick_reply_and_no_full_turn(self) -> None:
        self.quick, self.full = json.dumps({"reply": "Hello!", "full_agent": "stop"}), "unused"
        said = self.two.send("hi")
        self.assertEqual((said["answer"], said["turn"]), ("Hello!", ""))
        self.assertEqual([role for role, _ in self.asked], ["fast_reply"])
        self.assertEqual(self._messages(said["conversation_id"])[-1]["metadata"]["layer"], "fast")

    def test_the_quick_model_has_no_tools_and_is_told_to_leave_the_full_agent_running(self) -> None:
        self.quick, self.full = json.dumps({"reply": "On it.", "full_agent": "run"}), "Done."
        said = self.two.send("Remember that I take tea at four")
        self._done(said["turn"])
        request = self.asked[0][1]
        self.assertEqual(request.tools, [], "only the full agent acts")
        self.assertIn("WHENEVER A TOOL MIGHT BE NEEDED", request.system)
        self.assertIn('"remember"', request.system)
        self.assertIn("more will follow below", request.system)
        self.assertIn(self.app.agent._system_prompt()[:60], request.system, "who it is, as ever")
        self.assertGreater(request.system.index("THE QUICK VOICE"), request.system.index("Workspace:"),
                           "what is different comes last, and holds")

    def test_the_quick_model_is_given_the_picture_of_the_screen_too(self) -> None:
        """The user, 2026-11-11: it said only "Hi Fang," of an email was visible - the window's
        own text held its first line, the picture all of it, and the picture went to the full
        agent alone."""
        self.quick, self.full = json.dumps({"reply": "Looking.", "full_agent": "run"}), "Read."
        picture = {"media_type": "image/png", "data": "aGVsbG8="}
        said = self.two.send("What does this email mean?", front="\n\n[On the screen: Hi Fang,]",
                             seen=[picture])
        self._done(said["turn"])
        content = self.asked[0][1].messages[-1]["content"]
        self.assertIsInstance(content, list)
        self.assertIn("Hi Fang,", content[0]["text"])
        self.assertEqual(content[1]["source"]["data"], "aGVsbG8=")
        self.quick = json.dumps({"reply": "Hello.", "full_agent": "stop"})
        self.two.send("hi again")
        quick = [request for role, request in self.asked if role == "fast_reply"][-1]
        self.assertIsInstance(quick.messages[-1]["content"], str, "no picture: words, as ever")

    def test_the_full_agent_reads_what_was_said_and_is_folded_under_it(self) -> None:
        self.quick, self.full = json.dumps({"reply": "Looking.", "full_agent": "run"}), "The whole answer."
        said = self.two.send("What is on today?")
        state = self._done(said["turn"])
        self.assertEqual((state["answer"], state["kind"]), ("The whole answer.", "more"))
        full = self.asked[1][1]
        self.assertEqual(self.asked[1][0], "primary")
        self.assertIn("Looking.", str(full.messages[-1]["content"]))
        messages = self._messages(said["conversation_id"])
        self.assertEqual([(one["role"], one["metadata"].get("layer")) for one in messages],
                         [("user", None), ("assistant", "fast"), ("assistant", "slow")],
                         "the user's words written once")

    def test_what_it_changed_is_said_on_the_line_of_its_fold(self) -> None:
        store = self.app.monitor.store
        self.quick, self.full = json.dumps({"reply": "On it.", "full_agent": "run"}), "Done."
        real = self.app.agent._reason

        def acting(*args, **kwargs):
            store.add_quiet("In a meeting", {"signal": "activity", "op": "==", "value": "meeting"},
                            "chat", "asked")
            said = real(*args, **kwargs)
            said["tool_calls"] = [{"tool": "lesson_add"}]
            return said
        self.app.agent._reason = acting
        said = self.two.send("Never remind me in meetings")
        did = self._done(said["turn"])["did"]
        self.assertEqual((did["remembered"], did["quiet"], did["rules"]), (1, 1, 0))
        self.assertEqual(len(did["changes"]), 1, "to be taken back")
        kept = self._messages(said["conversation_id"])[-1]
        self.assertEqual(kept["metadata"]["did"], did)

    def test_nothing_to_add_nothing_shown(self) -> None:
        self.quick, self.full = json.dumps({"reply": "Paris.", "full_agent": "run"}), "NOTHING_TO_ADD"
        said = self.two.send("Capital of France?")
        state = self._done(said["turn"])
        self.assertTrue(state["nothing"])
        self.assertEqual(state["answer"], "")

    def test_a_correction_is_said_to_be_one(self) -> None:
        self.quick = json.dumps({"reply": "It is Lyon.", "full_agent": "run"})
        self.full = "[CORRECTION] It is Paris."
        said = self.two.send("Capital of France?")
        state = self._done(said["turn"])
        self.assertEqual((state["answer"], state["kind"]), ("It is Paris.", "correction"))

    def test_a_file_always_goes_on_whatever_the_quick_model_says(self) -> None:
        self.quick, self.full = json.dumps({"reply": "Nice.", "full_agent": "stop"}), "Read it."
        one = self.app.chat_files.save("notes.txt", b"plant in spring")
        said = self.two.send("Look", attachments=[one["id"]])
        self.assertTrue(said["turn"], "it cannot read it")
        self.assertIn("notes.txt", self.asked[0][1].messages[-1]["content"])
        self._done(said["turn"])
        self.assertIn("plant in spring", str(self.asked[-1][1].messages[-1]["content"]))

    def test_the_quick_model_failing_the_full_agent_answers(self) -> None:
        self.quick, self.full = RuntimeError("no"), "The answer."
        said = self.two.send("Hello?")
        self.assertEqual(said["answer"], "")
        self.assertEqual(self._done(said["turn"])["answer"], "The answer.")

    def test_not_json_the_full_agent_goes_on(self) -> None:
        self.quick, self.full = "Just words.", "More."
        said = self.two.send("Hello?")
        self.assertEqual(said["answer"], "Just words.")
        self.assertTrue(said["turn"])
        self._done(said["turn"])

    def test_written_while_it_works_they_are_taken_up_together(self) -> None:
        held, began = threading.Event(), threading.Event()
        real = self.app.agent.handle_message
        calls = []

        def slow(text, *args, **kwargs):
            calls.append(text)
            began.set()
            held.wait(5)
            return real(text, *args, **kwargs)
        self.app.agent.handle_message = slow
        self.quick, self.full = json.dumps({"reply": "Yes.", "full_agent": "run"}), "Answered."
        first = self.two.send("One")
        self.assertTrue(began.wait(5))
        second = self.two.send("Two", first["conversation_id"])
        third = self.two.send("Three", first["conversation_id"])
        self.assertTrue(second["answer"], "answered at once all the same")
        held.set()
        self.assertEqual(self._done(first["turn"])["answer"], "Answered.")
        self.assertEqual(self._done(second["turn"])["state"], "merged")
        self.assertEqual(self._done(third["turn"])["answer"], "Answered.")
        self.assertEqual(calls, ["One", "Two\n\nThree"])

    def test_a_message_sent_before_a_question_is_never_its_answer(self) -> None:
        """The user, 2026-11-09: "1" sent twice, six seconds apart; the first's full answer asked
        "Do you want that?" nine seconds after the second was sent - and the second was taken as
        its yes. Handed to the model, the second stands where it was sent."""
        held, began = threading.Event(), threading.Event()
        real = self.app.agent.handle_message
        answers = iter(["Do you want me to add the talk to your calendar?", "Not sure what 1 means."])

        def slow(text, *args, **kwargs):
            began.set()
            held.wait(5)
            self.full = next(answers)
            return real(text, *args, **kwargs)
        self.app.agent.handle_message = slow
        self.quick = json.dumps({"reply": "Looking.", "full_agent": "run"})
        first = self.two.send("1")
        self.assertTrue(began.wait(5))
        second = self.two.send("1", first["conversation_id"])
        time.sleep(0.05)
        held.set()
        self._done(first["turn"])
        self._done(second["turn"])
        request = self.asked[-1][1]
        said = str(request.messages[-1]["content"])
        self.assertIn("written BEFORE that answer existed", said)
        self.assertIn("Do you want me to add the talk", said, "told apart, as written after it")
        history = " ".join(str(one["content"]) for one in request.messages[:-1])
        self.assertNotIn("Do you want me to add the talk", history, "not before it, as asked first")
        # A message with nothing written while it waited is told nothing of the kind.
        self.assertNotIn("written BEFORE", str(self.asked[1][1].messages[-1]["content"]))

    def test_the_heartbeat_does_not_learn_from_the_quick_reply(self) -> None:
        self.quick, self.full = json.dumps({"reply": "A guess.", "full_agent": "stop"}), "x"
        said = self.two.send("hi")
        source = (Path(support.ROOT) / "personalized_agent" / "heartbeat" / "core" / "heartbeat.py")
        self.assertIn('meta.get("layer") == "fast"', source.read_text(encoding="utf-8"))
        self.assertTrue(said["message_id"])

    def test_it_thinks_as_little_as_the_model_allows(self) -> None:
        from unittest import mock
        models = self.app.agent.models.real
        for offered, least in ((["low", "medium", "high"], "low"),
                               (["minimal", "low", "medium", "high"], "minimal"),
                               (["none", "low", "high"], "none"), ([], "")):
            with mock.patch.object(models, "efforts_of", return_value=offered):
                self.assertEqual(models.lowest_effort("any", "any"), least)

    def test_the_small_model_is_the_same_one_at_low_effort_unless_chosen(self) -> None:
        """The user, 2026-11-11: "default是low effort模式，模型和正常保持一致"."""
        models = self.app.agent.models.real
        self.app.config.set("primary_provider", "claude_code")
        self.app.config.set("primary_model", "claude-opus-5-5")
        self.assertEqual(models.resolve("fast_reply").model_id, models.resolve("primary").model_id)
        self.app.config.set("chat_fast_model", "claude_code/claude-sonnet-5-5")
        self.assertEqual(models.resolve("fast_reply").model_id, "claude-sonnet-5-5")


class TheReplyAsItIsWrittenTests(unittest.TestCase):
    """THE USER, 2026-11-04: the first words at once, not the whole at the end."""

    def _stream(self, told):
        from personalized_agent.models.base import ModelRequest
        from personalized_agent.models.providers.claude_code_provider import _Stream
        return _Stream(ModelRequest(messages=[], on_text=told), None, 60)

    def _line(self, event):
        return json.dumps({"type": "stream_event", "event": event})

    def test_each_piece_as_it_comes_and_a_new_message_begins_again(self) -> None:
        said = []
        stream = self._stream(said.append)
        piece = lambda text: {"type": "content_block_delta",
                              "delta": {"type": "text_delta", "text": text}}
        stream.line(self._line({"type": "message_start"}))
        stream.line(self._line(piece("Let me ")))
        stream.line(self._line(piece("look.")))
        stream.line(self._line({"type": "content_block_delta",
                                "delta": {"type": "input_json_delta", "partial_json": "{}"}}))
        stream.line(self._line({"type": "message_start"}))
        stream.line(self._line(piece("Found it.")))
        self.assertEqual(said, ["", "Let me ", "Let me look.", "", "Found it."])

    def test_a_bad_line_is_nothing(self) -> None:
        stream = self._stream(lambda text: 1 / 0)
        stream.line('{"stream_event": broken')
        stream.line(self._line({"type": "message_start"}))     # its failure is no failure here

    def test_shown_with_the_progress_but_never_a_tool_call_written_out(self) -> None:
        from personalized_agent.agent.core.agent_core import AgentCore
        from personalized_agent.common.observability.metrics import Observability
        core = AgentCore.__new__(AgentCore)
        core.observability = SimpleNamespace(task_text=lambda task, text: seen.append(text))
        seen, told = [], []
        core._reply_so_far("t", "Hello", told.append)
        core._reply_so_far("t", '{"tool": "x"', told.append)
        self.assertEqual((seen, told), (["Hello"], ["Hello"]))
        self.assertTrue(hasattr(Observability, "task_text"))


class ThePictureReachesTheModelTests(unittest.TestCase):
    """THE USER, 2026-11-04: through Claude Code a picture was said in words
    and never seen, and read again it came as a run of letters."""

    def test_claude_code_is_given_it_in_its_stream(self) -> None:
        from personalized_agent.models.providers.claude_code_provider import with_pictures
        picture = {"media_type": "image/png", "data": "QUJD"}
        command, given = with_pictures(["claude", "-p", "--model", "m"], "User: look", [picture])
        self.assertEqual(command, ["claude", "-p", "--input-format", "stream-json", "--model", "m"])
        said = json.loads(given)["message"]["content"]
        self.assertEqual(said[0], {"type": "text", "text": "User: look"})
        self.assertEqual(said[1]["source"]["data"], "QUJD")

    def test_a_call_with_no_picture_is_as_ever(self) -> None:
        from personalized_agent.models.providers.claude_code_provider import with_pictures
        self.assertEqual(with_pictures(["claude", "-p"], "User: hello", []),
                         (["claude", "-p"], "User: hello"))

    def test_a_tool_s_picture_goes_as_a_picture_through_the_bridge(self) -> None:
        from personalized_agent.models.base import ToolSpec
        from personalized_agent.models.providers.mcp_bridge import ToolBridge
        spec = ToolSpec("look", "Look", {"type": "object", "properties": {}}, read_only=True)
        answer = {"name": "bed.png", "_pictures": [{"media_type": "image/png", "data": "QUJD"}]}
        with ToolBridge([spec], lambda name, arguments: answer) as bridge:
            said = bridge._call("look", {})
        self.assertEqual(said["content"][1], {"type": "image", "data": "QUJD", "mimeType": "image/png"})
        self.assertNotIn("QUJD", said["content"][0]["text"], "no run of letters")


class DocumentPagesTests(unittest.TestCase):
    """THE USER, 2026-11-04: a slide's arrows never reached the model. Office
    itself is never run here."""

    def setUp(self) -> None:
        self.held = tempfile.TemporaryDirectory()
        self.here = Path(self.held.name)
        self.files = ChatFiles(self.here / "chat_files")

    def tearDown(self) -> None:
        self.held.cleanup()

    def _pdf(self, pages=2) -> bytes:
        from PIL import Image
        out = io.BytesIO()
        first, *rest = [Image.new("RGB", (300, 200), (10 * n, 90, 200)) for n in range(pages)]
        first.save(out, "PDF", save_all=True, append_images=rest)
        return out.getvalue()

    def _deck(self):
        from pptx import Presentation
        from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
        from pptx.util import Inches
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        one = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(2), Inches(1))
        one.text_frame.text = "Seeds"
        two = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(6), Inches(4), Inches(2), Inches(1))
        two.text_frame.text = "Harvest"
        line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, 0, 0, 100, 100)
        line.begin_connect(one, 3)
        line.end_connect(two, 1)
        out = io.BytesIO()
        deck.save(out)
        return out.getvalue()

    def test_the_pages_of_a_pdf_with_pictures_go_as_pictures(self) -> None:
        one = self.files.save("plan.pdf", self._pdf())
        said = self.files.for_the_model([one["id"]])
        self.assertEqual(len(said["pictures"]), 2)
        self.assertIn("pages 1, 2 of 2 are shown as pictures", said["text"])

    def test_a_page_of_words_alone_is_not_drawn(self) -> None:
        from unittest import mock
        from personalized_agent.runtime import document_pages
        one = self.files.save("plan.pdf", self._pdf())
        with mock.patch.object(document_pages, "_has_a_figure", return_value=False):
            said = self.files.for_the_model([one["id"]])
        self.assertEqual(said["pictures"], [])

    def test_at_most_so_many_in_a_message(self) -> None:
        one = self.files.save("long.pdf", self._pdf(30))
        said = self.files.for_the_model([one["id"]])
        self.assertEqual(len(said["pictures"]), chat_files.MOST_PAGES_IN_A_MESSAGE)
        self.assertIn("of 30 are shown", said["text"])

    def test_drawn_once(self) -> None:
        from unittest import mock
        from personalized_agent.runtime import document_pages
        one = self.files.save("plan.pdf", self._pdf())
        self.files.for_the_model([one["id"]])
        with mock.patch.object(document_pages, "_draw", side_effect=AssertionError("again")):
            self.assertEqual(len(self.files.for_the_model([one["id"]])["pictures"]), 2)

    def test_not_sent_again_by_itself_but_there_to_ask_for(self) -> None:
        one = self.files.save("plan.pdf", self._pdf())
        self.files.for_the_model([one["id"]])
        later = self.files.later([{"content": "x", "metadata": {"attachments": [one]}}])[0]
        self.assertEqual(later["pictures"], [])
        self.assertIn("pages 1, 2 can be looked at", later["content"])
        page = self.files.read(one["id"], page=2)
        self.assertEqual(page["of"], 2)
        self.assertEqual(len(page["_pictures"]), 1)
        self.assertIn("error", self.files.read(one["id"], page=9))

    def test_a_document_showing_a_password_is_not_drawn(self) -> None:
        from unittest import mock
        one = self.files.save("plan.pdf", self._pdf())
        with mock.patch("personalized_agent.runtime.documents.shows_credentials", return_value=True):
            self.assertEqual(self.files.for_the_model([one["id"]])["pictures"], [])

    def test_nothing_to_draw_a_deck_with_its_layout_is_said_in_words(self) -> None:
        from unittest import mock
        from personalized_agent.runtime import document_pages
        one = self.files.save("flow.pptx", self._deck())
        with mock.patch.object(document_pages, "_office_pdf", return_value=""):
            said = self.files.for_the_model([one["id"]])
        self.assertEqual(said["pictures"], [])
        self.assertIn('"Seeds" at 10% across', said["text"])
        self.assertIn('from "Seeds" to "Harvest"', said["text"])

    def test_a_deck_drawn_by_office_is_every_page(self) -> None:
        from unittest import mock
        from personalized_agent.runtime import document_pages
        one = self.files.save("flow.pptx", self._deck())

        def made(_path, pdf):
            pdf.write_bytes(self._pdf())
            return "powerpoint"
        with mock.patch.object(document_pages, "_office_pdf", side_effect=made), \
                mock.patch.object(document_pages, "_has_a_figure", return_value=False):
            said = self.files.for_the_model([one["id"]])
        self.assertEqual(len(said["pictures"]), 2, "a deck: every page, figures or not")
        self.assertFalse((self.files.folder / one["id"] / "pages" / "document.pdf").exists())

    def test_no_macro_runs_office_turns_them_off_before_it_opens_a_file(self) -> None:
        # A file opened by a program has its macros allowed unless told.
        from personalized_agent.runtime import document_pages
        for program, opening in ((document_pages.POWERPOINT, "Presentations.Open"),
                                 (document_pages.WORD, "Documents.Open")):
            off = program.index("$app.AutomationSecurity = 3")
            checked = program.index("-ne 3")
            self.assertLess(off, checked)
            self.assertLess(checked, program.index(opening), "read back, then opened")
        self.assertIn("finally { $app.AutomationSecurity = $was }", document_pages.POWERPOINT,
                      "the user's own PowerPoint left as it was")

    def test_its_pages_go_with_it(self) -> None:
        one = self.files.save("plan.pdf", self._pdf())
        self.files.for_the_model([one["id"]])
        self.files.forget([one["id"]])
        self.assertFalse((self.files.folder / one["id"]).exists())


class GotReadyInTheBackgroundTests(unittest.TestCase):
    """THE USER, 2026-11-04: read from when it is uploaded; taken off, it stops."""

    def setUp(self) -> None:
        self.held = tempfile.TemporaryDirectory()
        self.files = ChatFiles(Path(self.held.name) / "chat_files", prepares=True)

    def tearDown(self) -> None:
        self.files.stop()
        time.sleep(0.05)
        self.held.cleanup()

    def _pdf(self):
        from PIL import Image
        out = io.BytesIO()
        Image.new("RGB", (300, 200), (10, 90, 200)).save(out, "PDF")
        return out.getvalue()

    def test_ready_before_the_message_is_sent(self) -> None:
        from unittest import mock
        from personalized_agent.runtime import document_pages
        one = self.files.save("plan.pdf", self._pdf())
        for _ in range(3000):
            if (self.files.folder / one["id"] / "pages" / "pages.json").exists():
                break
            time.sleep(0.02)
        with mock.patch.object(document_pages, "_draw", side_effect=AssertionError("at send")):
            self.assertEqual(len(self.files.for_the_model([one["id"]])["pictures"]), 1)

    def test_a_message_waits_for_what_is_left(self) -> None:
        from unittest import mock
        from personalized_agent.runtime import document_pages
        real = document_pages._draw

        def slow(*args):
            time.sleep(0.3)
            return real(*args)
        with mock.patch.object(document_pages, "_draw", side_effect=slow):
            one = self.files.save("plan.pdf", self._pdf())
            self.assertEqual(len(self.files.for_the_model([one["id"]])["pictures"]), 1)

    def test_taken_off_it_stops_and_leaves_nothing(self) -> None:
        from unittest import mock
        from personalized_agent.runtime import document_pages
        going, may_go, drew = threading.Event(), threading.Event(), []

        def held_up(path, pdf):
            going.set()
            may_go.wait(5)
            pdf.write_bytes(self._pdf())
            return "powerpoint"
        real = document_pages._draw
        with mock.patch.object(document_pages, "_office_pdf", side_effect=held_up), \
                mock.patch.object(document_pages, "_draw",
                                  side_effect=lambda *a: drew.append(1) or real(*a)):
            one = self.files.save("flow.pptx", b"not really a deck")
            self.assertTrue(going.wait(5), "it began by itself")
            self.files.forget([one["id"]])
            may_go.set()
            for _ in range(200):
                if one["id"] not in self.files._jobs:
                    break
                time.sleep(0.02)
        self.assertEqual(drew, [], "no page drawn after it was taken off")
        self.assertFalse((self.files.folder / one["id"]).exists(), "and nothing left")

    def test_waiting_its_turn_it_never_begins(self) -> None:
        from unittest import mock
        from personalized_agent.runtime import document_pages
        release, ran = threading.Event(), []
        self.files._pool.submit(release.wait, 5)
        self.files._pool.submit(release.wait, 5)         # both threads busy
        with mock.patch.object(document_pages, "_office_pdf",
                               side_effect=lambda *a: ran.append(1) or ""):
            one = self.files.save("flow.pptx", b"not really a deck")
            self.files.forget([one["id"]])
            release.set()
            time.sleep(0.2)
        self.assertEqual(ran, [])


class HeldBackAndLetGoTests(unittest.TestCase):

    def setUp(self) -> None:
        self.held = tempfile.TemporaryDirectory()
        self.files = ChatFiles(Path(self.held.name) / "chat_files")

    def tearDown(self) -> None:
        self.held.cleanup()

    def test_a_picture_showing_a_password_is_not_given(self) -> None:
        from unittest import mock
        one = self.files.save("login.png", _png())
        with mock.patch("personalized_agent.runtime.documents.held_back", return_value=True):
            said = self.files.for_the_model([one["id"]])
            self.assertEqual(said["pictures"], [])
            self.assertIn(chat_files.HELD_BACK, said["text"])
            later = self.files.later([{"content": "x", "metadata": {"attachments": [one]}}])
            self.assertEqual(later[0]["pictures"], [])
            again = self.files.read(one["id"])
            self.assertNotIn("_pictures", again)

    def test_let_go_with_what_was_read_of_it(self) -> None:
        from unittest import mock
        one = self.files.save("notes.txt", b"plant in spring")
        with mock.patch("personalized_agent.runtime.documents.forget_copy") as forgot:
            self.files.forget([one["id"], "chatfile_gone", "../outside"])
        forgot.assert_called_once()
        self.assertFalse((self.files.folder / one["id"]).exists())

    def test_what_was_never_sent_goes_a_day_on(self) -> None:
        import os
        import time
        sent = self.files.save("a.txt", b"sent")
        unsent = self.files.save("b.txt", b"unsent")
        fresh = self.files.save("c.txt", b"just attached")
        old = time.time() - chat_files.UNSENT_SECONDS - 60
        for one in (sent, unsent):
            os.utime(self.files.folder / one["id"], (old, old))
        self.files.forget_unsent([sent["id"]])
        kept = {one.name for one in self.files.folder.iterdir()}
        self.assertEqual(kept, {sent["id"], fresh["id"]})


class ClipboardTests(unittest.TestCase):
    """THE USER, 2026-11-04: a WeChat screenshot pasted as it is. The real
    clipboard is never touched here."""

    def setUp(self) -> None:
        self.held = tempfile.TemporaryDirectory()
        self.here = Path(self.held.name)
        self.files = ChatFiles(self.here / "chat_files")

    def tearDown(self) -> None:
        self.held.cleanup()

    def test_a_picture_there_is_kept_under_a_name_of_its_own(self) -> None:
        said = self.files.from_clipboard(read=lambda with_text: [_png()])
        one = said["files"][0]
        self.assertTrue(one["picture"])
        self.assertRegex(one["name"], r"^screenshot-\d{8}-\d{6}\.png$")

    def test_a_form_the_models_do_not_take_is_made_a_png(self) -> None:
        from PIL import Image
        out = io.BytesIO()
        Image.new("RGB", (3, 3), (1, 2, 3)).save(out, "BMP")
        one = self.files.from_clipboard(read=lambda with_text: [out.getvalue()])["files"][0]
        self.assertEqual(self.files.file(one["id"])[0][:4], b"\x89PNG")

    def test_files_copied(self) -> None:
        note = self.here / "notes.txt"
        note.write_text("plant in spring", encoding="utf-8")
        said = self.files.from_clipboard(read=lambda with_text: [note, self.here])
        self.assertEqual([one["name"] for one in said["files"]], ["notes.txt"])
        self.assertEqual(said["skipped"], [self.here.name], "a folder is not attached")

    def test_what_is_no_picture_is_said(self) -> None:
        said = self.files.from_clipboard(read=lambda with_text: [b"not a picture"])
        self.assertEqual((said["files"], said["skipped"]), ([], ["picture"]))

    def test_with_words_it_is_asked_for_so(self) -> None:
        asked = []
        self.files.from_clipboard(True, read=lambda with_text: asked.append(with_text) or [])
        self.assertEqual(asked, [True])

    def test_the_pictures_of_an_html_copy(self) -> None:
        from personalized_agent.platform_layer.clipboard import pictures_in_html
        picture = self.here / "a b.png"
        picture.write_bytes(_png())
        (self.here / "secret.txt").write_text("no", encoding="utf-8")
        url = picture.as_uri()
        written = base64.b64encode(_png()).decode("ascii")
        found = pictures_in_html(
            f'<p>x</p><img width=3 src="{url}"><img src="{(self.here / "secret.txt").as_uri()}">'
            f'<IMG SRC="https://example.org/a.png"><img src="data:image/png;base64,{written}">'.encode())
        self.assertEqual(found, [picture, _png()], "none from the web, nothing but pictures")
        self.assertEqual(pictures_in_html(None), [])


class _Model:
    def __init__(self):
        self.asked = []

    def invoke(self, request, role="primary"):
        self.asked.append(request)
        return SimpleNamespace(text="Seen.", tool_calls=[], usage=None, stop_reason="end_turn",
                               content=[{"type": "text", "text": "Seen."}])

    def __getattr__(self, name):
        return getattr(self.real, name)


class ThroughTheChatTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.model = _Model()
        self.model.real = self.app.agent.models
        self.app.agent.models = self.model
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        from personalized_agent.api.server import ApiServer
        self.server = ApiServer(self.app)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    def _post(self, path, payload):
        request = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        # A ceiling: on a full run a request has waited more than thirty seconds for its turn.
        with urllib.request.urlopen(keyed(self.app, request), timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))

    def _upload(self, name, data):
        return self._post("/api/chat/upload", {"name": name,
                                               "data": base64.b64encode(data).decode("ascii")})

    def _last_user(self):
        return [one for one in self.model.asked[-1].messages if one["role"] == "user"][-1]

    def test_kept_in_the_agents_own_folder(self) -> None:
        one = self._upload("notes.txt", b"plant in spring")
        self.assertTrue(str(self.app.chat_files._path(one["id"])).startswith(
            str(self.app.paths.data_dir)))

    def test_a_bigger_file_than_a_small_body_gets_through(self) -> None:
        one = self._upload("big.txt", b"z" * (3 * 1024 * 1024))
        self.assertEqual(one["size"], 3 * 1024 * 1024)

    def test_served_back_to_the_page(self) -> None:
        one = self._upload("bed.png", _png())
        with urllib.request.urlopen(keyed(self.app, f"{self.base}/api/chat/file?id={one['id']}"),
                                    timeout=20) as response:
            self.assertEqual(response.read(), _png())
            self.assertEqual(response.headers.get("Content-Type"), "image/png")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(keyed(self.app, f"{self.base}/api/chat/file?id=chatfile_gone"),
                                   timeout=20)
        self.assertEqual(caught.exception.code, 404)

    def test_the_model_is_given_the_words_and_the_picture(self) -> None:
        note = self._upload("notes.txt", b"plant in spring")
        bed = self._upload("bed.png", _png())
        said = self._post("/api/chat", {"message": "What do you make of these?",
                                        "attachments": [note["id"], bed["id"]]})
        self.assertEqual(said["answer"], "Seen.")
        content = self._last_user()["content"]
        self.assertIn("plant in spring", content[0]["text"])
        self.assertEqual(content[1]["type"], "image")
        kept = self.app.conversations.messages(said["conversation_id"])[0]
        self.assertEqual(kept["content"], "What do you make of these?", "the user's words alone")
        self.assertEqual([one["name"] for one in kept["metadata"]["attachments"]],
                         ["notes.txt", "bed.png"], "and its files beside them")

    def test_the_next_turn_still_knows_of_it(self) -> None:
        note = self._upload("notes.txt", b"plant in spring")
        said = self._post("/api/chat", {"message": "Read this", "attachments": [note["id"]]})
        self._post("/api/chat", {"message": "And when?", "conversation_id": said["conversation_id"]})
        earlier = " ".join(str(one["content"]) for one in self.model.asked[-1].messages)
        self.assertIn(f"[attached: notes.txt, id {note['id']}]", earlier)
        self.assertIn("plant in spring", earlier)

    def test_the_next_turn_still_sees_the_picture(self) -> None:
        bed = self._upload("bed.png", _png())
        said = self._post("/api/chat", {"message": "Look", "attachments": [bed["id"]]})
        self._post("/api/chat", {"message": "What is on its left?",
                                 "conversation_id": said["conversation_id"]})
        before = [one for one in self.model.asked[-1].messages if one["role"] == "user"][0]
        self.assertEqual(before["content"][1]["type"], "image")

    def test_the_model_can_read_a_file_again(self) -> None:
        note = self._upload("notes.txt", b"plant in spring")
        said = self.app.agent.tools.call("attached_file_read", {"id": note["id"]})
        self.assertEqual(said["text"], "plant in spring")
        self.assertIn("error", self.app.agent.tools.call("attached_file_read", {"id": "../x"}))

    def test_a_file_alone_is_a_message_and_nothing_at_all_is_not(self) -> None:
        note = self._upload("notes.txt", b"plant in spring")
        self.assertEqual(self._post("/api/chat", {"message": "", "attachments": [note["id"]]})["answer"],
                         "Seen.")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._post("/api/chat", {"message": "  "})
        self.assertEqual(caught.exception.code, 400)

    def test_pasted_when_the_page_could_not_read_it(self) -> None:
        from unittest import mock
        with mock.patch("personalized_agent.platform_layer.clipboard.files_on_it",
                        return_value=[_png()]) as read:
            said = self._post("/api/chat/clipboard", {"with_text": False})
        read.assert_called_once_with(False)
        self.assertTrue(said["files"][0]["picture"])

    def test_a_file_at_ed_is_given_with_the_message(self) -> None:
        bed = self._upload("bed.png", _png())
        note = self._upload("notes.txt", b"plant in spring")
        said = self._post("/api/chat", {"message": "Look", "attachments": [bed["id"], note["id"]]})
        self._post("/api/chat", {"message": "What about @bed.png ?", "mentions": [bed["id"]],
                                 "conversation_id": said["conversation_id"]})
        content = self._last_user()["content"]
        self.assertIn("@bed.png refers to the file of that name", content[0]["text"])
        self.assertEqual(content[1]["type"], "image", "as if attached again")
        kept = self.app.conversations.messages(said["conversation_id"])[-2]
        self.assertEqual(kept["content"], "What about @bed.png ?")
        self.assertNotIn("attachments", kept["metadata"], "not a second copy of it")

    def test_only_a_file_of_that_conversation_can_be_at_ed(self) -> None:
        secret = self._upload("other.txt", b"from another conversation")
        self._post("/api/chat", {"message": "Read", "attachments": [secret["id"]]})
        self._post("/api/chat", {"message": "And @other.txt", "mentions": [secret["id"], "chatfile_gone"]})
        self.assertNotIn("from another conversation", str(self._last_user()["content"]))

    def test_at_ing_a_file_being_attached_says_so_once(self) -> None:
        note = self._upload("notes.txt", b"plant in spring")
        self._post("/api/chat", {"message": "See @notes.txt", "attachments": [note["id"]],
                                 "mentions": [note["id"]]})
        content = str(self._last_user()["content"])
        self.assertEqual(content.count("plant in spring"), 1)
        self.assertIn("@notes.txt refers", content)

    def test_a_conversation_deleted_takes_its_files_with_it(self) -> None:
        note = self._upload("notes.txt", b"plant in spring")
        other = self._upload("other.txt", b"kept")
        said = self._post("/api/chat", {"message": "Read", "attachments": [note["id"]]})
        kept = self._post("/api/chat", {"message": "Read", "attachments": [other["id"]]})
        self._post("/api/conversation/delete", {"id": said["conversation_id"]})
        with self.assertRaises(KeyError):
            self.app.chat_files.described(note["id"])
        self.assertEqual(self.app.chat_files.described(other["id"])["name"], "other.txt")
        self.assertEqual(self.app.conversations.attachments(kept["conversation_id"]), [other["id"]])

    def test_a_conversation_deleted_takes_the_records_of_its_turns(self) -> None:
        gone = self._post("/api/chat", {"message": "Words to forget"})
        kept = self._post("/api/chat", {"message": "Words to keep"})

        def said():
            return json.dumps([dict(one) for one in self.app.db.query("SELECT * FROM raw_edits")],
                              default=str)
        self.assertIn("Words to forget", said())
        self._post("/api/conversation/delete", {"id": gone["conversation_id"]})
        self.assertNotIn("Words to forget", said())
        self.assertIn("Words to keep", said(), "the other conversation's stay")
        self.assertTrue(kept["conversation_id"])

    def test_a_conversation_deleted_takes_the_copies_kept_of_its_messages(self) -> None:
        said = self._post("/api/chat", {"message": "Words to forget"})
        message = self.app.conversations.messages(said["conversation_id"])[0]["id"]
        folder = self.app.paths.data_dir / "test_refs" / "materials"
        folder.mkdir(parents=True)
        copy = folder / "a.txt"
        copy.write_text("Words to forget", encoding="utf-8")
        outside = folder.parent / "b.txt"
        outside.write_text("not a copy", encoding="utf-8")
        self.app.db.execute(
            "INSERT INTO state_materials (state_id, event_id, kind, title, location, copies, "
            "linked_by, created_at) VALUES ('s1', ?, 'message', 't', 'l', ?, 'x', 'x')",
            (message, json.dumps({"text": str(copy), "other": str(outside)})))
        self._post("/api/conversation/delete", {"id": said["conversation_id"]})
        self.assertFalse(copy.exists())
        self.assertTrue(outside.exists(), "only what is under materials")
        self.assertEqual(self.app.db.query("SELECT * FROM state_materials"), [])

    def test_taken_off_before_it_is_sent_it_is_gone_and_one_sent_stays(self) -> None:
        note = self._upload("notes.txt", b"plant in spring")
        self._post("/api/chat/file/remove", {"id": note["id"]})
        with self.assertRaises(KeyError):
            self.app.chat_files.described(note["id"])
        sent = self._upload("kept.txt", b"kept")
        self._post("/api/chat", {"message": "Read", "attachments": [sent["id"]]})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._post("/api/chat/file/remove", {"id": sent["id"]})
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.app.chat_files.described(sent["id"])["name"], "kept.txt")

    def test_the_record_of_a_turn_has_the_users_words_not_the_files(self) -> None:
        note = self._upload("notes.txt", b"a private line")
        self._post("/api/chat", {"message": "Read this", "attachments": [note["id"]]})
        rows = self.app.db.query("SELECT * FROM raw_edits")
        self.assertTrue(rows)
        self.assertNotIn("a private line", json.dumps([dict(one) for one in rows], default=str))

    def test_a_file_gone_is_said(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._post("/api/chat", {"message": "Read", "attachments": ["chatfile_gone"]})
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.model.asked, [])


if __name__ == "__main__":
    unittest.main()

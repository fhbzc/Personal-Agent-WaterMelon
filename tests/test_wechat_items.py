"""WeChat's messages one by one on Windows (platform_layer/wechat_items.py):
the words from Automation's list items, whose side from the picture. Every
picture here is drawn by the test; every name and message is made up."""

import unittest

from PIL import Image, ImageDraw

from personalized_agent.platform_layer.wechat_items import parse_wechat_items

WIDTH, HEIGHT = 1000, 600
CHAT = (300, 80, 690, 400)             # the message list: x, y, width, height
HEADER = {"text": "Example Friend", "box": (320, 30, 200, 30)}


def window(dark=False):
    background = (30, 30, 31) if dark else (237, 237, 237)
    picture = Image.new("RGB", (WIDTH, HEIGHT), background)
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 0, 299, HEIGHT), fill=(46, 46, 46) if dark else (230, 230, 230))
    return picture, draw, dark


def other_says(draw, dark, top, width=180):
    avatar = (310, top, 350, top + 40)
    draw.rectangle(avatar, fill=(200, 80, 40))
    draw.rectangle((avatar[0] + 8, top + 8, avatar[0] + 20, top + 20), fill=(30, 90, 200))
    draw.rounded_rectangle((362, top, 362 + width, top + 40), 6,
                           fill=(44, 44, 44) if dark else (255, 255, 255))
    draw.text((372, top + 12), "....", fill=(220, 220, 220) if dark else (20, 20, 20))


def i_say(draw, top, width=160):
    right = 300 + 690
    draw.rectangle((right - 50, top, right - 10, top + 40), fill=(60, 120, 200))
    draw.rectangle((right - 42, top + 8, right - 30, top + 20), fill=(240, 200, 40))
    draw.rounded_rectangle((right - 62 - width, top, right - 62, top + 40), 6, fill=(149, 236, 105))
    draw.text((right - 52 - width, top + 12), "....", fill=(20, 20, 20))


def item(text, top, height=60):
    return {"text": text, "box": (CHAT[0], top - 10, CHAT[2], height)}


class TellingThemApartTests(unittest.TestCase):

    def check_a_conversation(self, dark):
        picture, draw, dark = window(dark)
        other_says(draw, dark, 110)
        draw.text((610, 190), "10:42", fill=(150, 150, 150))
        i_say(draw, 240)
        draw.rectangle((990 - 50, 330, 990 - 10, 370), fill=(60, 120, 200))
        draw.rectangle((990 - 42, 338, 990 - 30, 350), fill=(240, 200, 40))
        draw.rectangle((700, 320, 920, 440), fill=(150, 80, 160))    # a photograph sent
        for x in range(700, 920, 7):
            draw.line((x, 320, x + 30, 440), fill=(200, 60, 120))
        items = [item("Are we still on for Wednesday?", 110), item("10:42", 185, 40),
                 item("Yes, three o'clock", 240), item("[Photo]", 330, 120)]
        frame = parse_wechat_items(picture, CHAT, items, [HEADER])
        self.assertEqual((frame["layout_status"], frame["peer_name"]), ("recognized", "Example Friend"))
        said = [(m["sender_role"], m["sender_basis"], m["text"]) for m in frame["messages"]]
        self.assertEqual(said, [
            ("other", "left_anchored_flat_bubble", "Are we still on for Wednesday?"),
            ("self", "right_bubble", "Yes, three o'clock"),
            ("self", "right_avatar", "[Photo]")])
        self.assertEqual(frame["unresolved_text"], "10:42", "a time is nobody's message")
        for message in frame["messages"]:
            self.assertEqual((message["content_status"], message["ocr_confidence"], message["text_source"]),
                             ("visible_chat_bubble", 100.0, "accessibility"))
            self.assertTrue(all(0 <= v <= 1 for v in message["bbox"]))
        self.assertNotEqual(frame["peer_avatar"], frame["self_avatar"])
        return frame

    def test_light_mode(self):
        frame = self.check_a_conversation(dark=False)
        self.assertEqual(frame["schema_version"], "2.0")
        self.assertEqual(frame["source"], "wechat_accessibility")

    def test_dark_mode(self):
        self.check_a_conversation(dark=True)

    def test_a_large_window_is_read_at_a_smaller_size_with_the_same_answer(self):
        picture, draw, dark = window()
        other_says(draw, dark, 110)
        i_say(draw, 240)
        items = [item("Example question", 110), item("Example answer", 240)]
        big = picture.resize((WIDTH * 2, HEIGHT * 2))
        doubled = lambda box: tuple(v * 2 for v in box)
        frame = parse_wechat_items(big, doubled(CHAT),
                                   [{**one, "box": doubled(one["box"])} for one in items],
                                   [{**HEADER, "box": doubled(HEADER["box"])}])
        self.assertEqual([m["sender_role"] for m in frame["messages"]], ["other", "self"])

    def test_a_long_message_beside_a_scrollbar_is_still_theirs(self):
        # Measured live (2026-10-26): a message of 107 characters came out
        # nobody's - its band filled from the avatar to the list's edge.
        for dark in (False, True):
            picture, draw, dark = window(dark)
            other_says(draw, dark, 110, width=560)          # wrapped across the list
            draw.rectangle((976, 80, 984, 480), fill=(120, 120, 120) if dark else (200, 200, 200))
            i_say(draw, 240)
            frame = parse_wechat_items(picture, CHAT, [item("A long example message", 110),
                                                       item("Example answer", 240)], [HEADER])
            self.assertEqual([m["sender_role"] for m in frame["messages"]], ["other", "self"], dark)

    def test_the_last_items_box_reaching_the_lists_bottom(self):
        # Measured live (2026-10-26): the last item's box ran to the list's
        # bottom, five times its bubble's height.
        for dark in (False, True):
            picture, draw, dark = window(dark)
            i_say(draw, 120)
            other_says(draw, dark, 200, width=420)
            frame = parse_wechat_items(picture, CHAT, [
                item("Example question", 120),
                {"text": "A long example answer", "box": (CHAT[0], 190, CHAT[2], 290)}], [HEADER])
            self.assertEqual([(m["sender_role"], m["sender_basis"]) for m in frame["messages"]],
                             [("self", "right_bubble"), ("other", "left_anchored_flat_bubble")],
                             "its bubble found, not only its avatar")

    def test_the_row_under_the_pointer_is_no_bubble(self):
        # Measured live (2026-10-26): WeChat lit the row under the pointer
        # grey across the list; the user's green bubble beside it was missed
        # - and their avatar, dark on dark, was not found either.
        picture, draw, dark = window(dark=True)
        draw.rectangle((CHAT[0], 220, CHAT[0] + CHAT[2] - 1, 300), fill=(47, 47, 48))
        right = 300 + 690
        draw.rectangle((right - 50, 240, right - 10, 280), fill=(34, 34, 35))     # a dark avatar
        draw.rounded_rectangle((right - 62 - 160, 240, right - 62, 280), 6, fill=(112, 192, 144))
        other_says(draw, dark, 110)
        frame = parse_wechat_items(picture, CHAT, [item("Example question", 110),
                                                   item("A long example answer", 240)], [HEADER])
        self.assertEqual([(m["sender_role"], m["sender_basis"]) for m in frame["messages"]],
                         [("other", "left_anchored_flat_bubble"), ("self", "right_bubble")])
        # Theirs, under the pointer, is theirs still.
        picture, draw, dark = window(dark=True)
        draw.rectangle((CHAT[0], 100, CHAT[0] + CHAT[2] - 1, 170), fill=(47, 47, 48))
        other_says(draw, dark, 110)
        frame = parse_wechat_items(picture, CHAT, [item("Example question", 110)], [HEADER])
        self.assertEqual([m["sender_role"] for m in frame["messages"]], ["other"])

    def test_no_name_above_the_list_is_not_a_conversation(self):
        picture, draw, dark = window()
        other_says(draw, dark, 110)
        frame = parse_wechat_items(picture, CHAT, [item("Example", 110)],
                                   [{"text": "Weixin", "box": (320, 30, 100, 30)},
                                    {"text": "Example Friend", "box": (320, 200, 100, 30)}])
        self.assertEqual((frame["layout_status"], frame["layout_reason"]),
                         ("unrecognized", "chat_header_not_readable"))
        self.assertEqual(frame["messages"], [])

    def test_a_page_that_is_not_a_conversation(self):
        picture, _draw, _dark = window()
        frame = parse_wechat_items(picture, CHAT, [], [{"text": "朋友圈", "box": (320, 30, 100, 30)}])
        self.assertEqual(frame["layout_status"], "not_chat")

    def test_group_and_items_scrolled_out_of_sight(self):
        picture, draw, dark = window()
        other_says(draw, dark, 110)
        frame = parse_wechat_items(picture, CHAT, [item("Out of sight", 20, 40), item("Example", 110)],
                                   [{"text": "Example Group (12)", "box": (320, 30, 200, 30)}])
        self.assertEqual(frame["conversation_type"], "group")
        self.assertEqual([m["text"] for m in frame["messages"]], ["Example"])

    def test_no_list_says_so(self):
        picture, _draw, _dark = window()
        frame = parse_wechat_items(picture, (0, 0, 0, 0), [], [HEADER])
        self.assertEqual(frame["layout_reason"], "message_list_not_found")


class TheStoreTakesItTests(unittest.TestCase):

    def test_kept_once_however_often_it_is_seen(self):
        import sys
        sys.path.insert(0, "tests")
        from support import TemporaryAgent
        from personalized_agent.common.events.models import ListenerEvent
        picture, draw, dark = window()
        other_says(draw, dark, 110)
        i_say(draw, 240)
        frame = parse_wechat_items(picture, CHAT, [item("Example question", 110), item("Example answer", 240)],
                                   [HEADER])
        agent = TemporaryAgent()
        try:
            store = agent.app.raw_store
            for _ in range(2):
                event = ListenerEvent(event_type="screen.text", source="screen", application="Weixin.exe",
                                      data={"text": "Example question\nExample answer",
                                            "wechat_capture": {**frame, "scope_id": "visit:1:1"}})
                store.add_event(event)
            listed = store.wechat.list_conversations()["conversations"]
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["messages"], 2)
            self.assertEqual(store.count_events(), 1, "the second sight adds nothing")
        finally:
            agent.cleanup()


if __name__ == "__main__":
    unittest.main()


class OnAMacFromItsAccessibilityTests(unittest.TestCase):
    """A Mac's WeChat 4 gives Accessibility the same list (measured
    2026-10-27): its bubbles marked chat_bubble_item_view, a time not."""

    def test_read_from_the_tree_with_the_picture_at_twice_the_size(self) -> None:
        import io
        from personalized_agent.platform_layer.macos_window_text import FrontWindow, NativeReader
        picture, draw, dark = window()
        other_says(draw, dark, 110)
        draw.text((610, 190), "10:42", fill=(150, 150, 150))
        i_say(draw, 240)
        big = picture.resize((WIDTH * 2, HEIGHT * 2))      # a Retina window's picture
        out = io.BytesIO()
        big.save(out, format="PNG")
        # The tree, in screen points; the window at (100, 50) on the screen.
        at = lambda box: (box[0] + 100, box[1] + 50, box[2], box[3])
        bubble = lambda words, top: {"AXIdentifier": "chat_bubble_item_view", "AXValue": words,
                                     "box": at((CHAT[0], top - 10, CHAT[2], 60))}
        chat = {"AXIdentifier": "chat_message_list", "box": at(CHAT), "AXChildren": [
            bubble("Are we still on for Wednesday?", 110),
            {"AXValue": "10:42", "box": at((CHAT[0], 185, CHAT[2], 40))},
            bubble("Yes, three o'clock", 240)]}
        name = {"AXIdentifier": "content_view.left_ui_.current_chat_name_label",
                "AXValue": "Example Friend", "box": at(HEADER["box"])}
        tree = {"AXChildren": [name, chat]}
        native = NativeReader.__new__(NativeReader)
        native.attr = lambda element, key: element.get(key)
        native.rect = lambda element: element.get("box")
        front = FrontWindow("WeChat", "WeChat", 1, 2, (100, 50, WIDTH, HEIGHT), tree)
        text, capture = native.wechat_messages(front, out.getvalue())
        self.assertEqual(capture["peer_name"], "Example Friend")
        self.assertEqual([(m["sender_role"], m["text"]) for m in capture["messages"]],
                         [("other", "Are we still on for Wednesday?"), ("self", "Yes, three o'clock")])
        self.assertEqual(capture["unresolved_text"], "10:42", "no bubble: nobody's")
        self.assertIn("Yes, three o'clock", text)

    def test_no_list_falls_back_to_recognition(self) -> None:
        from personalized_agent.platform_layer.macos_window_text import FrontWindow, NativeReader
        native = NativeReader.__new__(NativeReader)
        native.attr = lambda element, key: element.get(key)
        native.rect = lambda element: element.get("box")
        front = FrontWindow("WeChat", "WeChat", 1, 2, (0, 0, WIDTH, HEIGHT), {"AXChildren": []})
        self.assertIsNone(native.wechat_messages(front, b""))

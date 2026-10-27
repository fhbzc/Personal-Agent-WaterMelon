"""WeChat's messages one by one on Linux (platform_layer/linux_wechat.py): the
window's picture, read by RapidOCR line by line with each line's box, told
apart into messages as on a Mac. RapidOCR is not here: a stand-in answers as
it does, from a window the test draws; every name and message is made up."""

import io
import sys
import types
import unittest
from unittest.mock import patch

from PIL import Image

from test_wechat_structure import frame

from personalized_agent.platform_layer import linux_wechat
from personalized_agent.platform_layer.base import ActiveWindow


def _rapid_from(ocr):
    """A stand-in RapidOCR: answers the drawn window's lines, each with its
    four corners and a score, whatever picture it is given."""
    lines = []
    for word in ocr["words"]:
        x, y, w, h = word["bbox"]
        lines.append([[[x, y], [x + w, y], [x + w, y + h], [x, y + h]], word["text"], .93])

    class RapidOCR:
        calls = 0

        def __call__(self, picture):
            RapidOCR.calls += 1
            assert isinstance(picture, bytes)
            return lines, [0.1, 0.1, 0.1]

    return RapidOCR


class Activity:
    """A Linux desktop with the drawn WeChat window in front."""

    def __init__(self, png, application="wechat"):
        self.picture = Image.open(io.BytesIO(png))
        self.front = ActiveWindow(application=application, title="微信", pid=4242)
        self.after = None
        self.pictures = 0

    def get_active_window(self):
        if self.after is not None and self.pictures:
            return self.after
        return self.front

    def front_window_image(self):
        self.pictures += 1
        return self.picture


class ReadingWeChatOnLinuxTests(unittest.TestCase):

    def setUp(self):
        self.png, ocr = frame()
        engine = _rapid_from(ocr)
        self.modules = patch.dict(sys.modules, {"rapidocr_onnxruntime": types.SimpleNamespace(RapidOCR=engine)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        linux_wechat._engine = None
        self.addCleanup(setattr, linux_wechat, "_engine", None)

    def test_each_line_with_its_box_and_confidence(self):
        layout = linux_wechat.read_layout(self.png)
        self.assertEqual((layout["width"], layout["height"]), (1000, 700))
        first = layout["words"][0]
        self.assertEqual((first["text"], first["bbox"], first["confidence"]),
                         ("小陈", [330, 26, 70, 16], 93.0))

    def test_the_messages_told_apart_as_on_a_mac(self):
        got = linux_wechat.wechat_in_front(Activity(self.png), lambda front: True)
        capture = got.wechat_capture
        self.assertEqual((got.how, got.application, got.capture_scope),
                         ("linux_rapidocr", "wechat", "visible_text_only"))
        self.assertEqual(capture["layout_status"], "recognized", capture["layout_reason"])
        self.assertEqual(capture["peer_name"], "小陈")
        self.assertEqual([m["sender_role"] for m in capture["messages"]],
                         ["other", "self", "self", "other"])
        self.assertEqual(capture["unresolved_text"], "13:50")
        self.assertNotIn("draft", str(capture), "what is being typed is never a message")

    def test_another_window_is_not_read_nor_pictured(self):
        activity = Activity(self.png, application="firefox")
        self.assertIsNone(linux_wechat.wechat_in_front(activity, lambda front: True))
        self.assertEqual(activity.pictures, 0)

    def test_turned_away_by_the_privacy_checks_before_the_picture(self):
        activity = Activity(self.png)
        self.assertIsNone(linux_wechat.wechat_in_front(activity, lambda front: False))
        self.assertEqual(activity.pictures, 0)

    def test_a_window_that_left_the_front_is_thrown_away(self):
        activity = Activity(self.png)
        activity.after = ActiveWindow(application="firefox", title="Example", pid=7)
        self.assertIsNone(linux_wechat.wechat_in_front(activity, lambda front: True))

    def test_without_rapidocr_it_says_so(self):
        from personalized_agent.platform_layer.linux import LinuxActivityProvider
        provider = LinuxActivityProvider()
        provider.get_active_window = lambda: ActiveWindow(application="wechat", title="微信", pid=1)
        with patch.dict(sys.modules, {"rapidocr_onnxruntime": None}):
            self.assertIsNone(provider.text_in_front_allowed(lambda front: True))
        self.assertIn("RapidOCR", provider.window_text_error)

    def test_one_engine_kept(self):
        first = linux_wechat._rapid()
        self.assertIs(linux_wechat._rapid(), first, "its models load once")


class WhatLinuxSaysItCanReadTests(unittest.TestCase):

    def test_wechat_only_and_only_with_its_reader(self):
        from personalized_agent.platform_layer.linux import LinuxPlatform
        platform = LinuxPlatform()
        platform.activity._xdotool = "/usr/bin/xdotool"
        with patch("personalized_agent.platform_layer.linux_wechat.rapid_available", return_value=True):
            said = platform.capabilities()
            self.assertTrue(said.window_text)
            self.assertEqual(said.extra["window_text_only"], "WeChat")
        with patch("personalized_agent.platform_layer.linux_wechat.rapid_available", return_value=False):
            self.assertFalse(platform.capabilities().window_text)


if __name__ == "__main__":
    unittest.main()

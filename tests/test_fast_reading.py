"""FAST SAMPLING (listener/providers/fast_reading.py): which windows are read
often, a window's fingerprint, and the pace of the looks."""

import unittest
from unittest.mock import patch

from PIL import Image

from personalized_agent.listener.providers import fast_reading
from personalized_agent.listener.providers.fast_reading import Pace, fingerprint, same_picture, wanted


class Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def stripes(every, shade=255):
    """Bands as wide as they are apart - a window's blocks, not hairlines."""
    picture = Image.new("L", (180, 160), 0)
    for x in range(0, 180, 2 * every):
        picture.paste(shade, (x, 0, min(180, x + every), 160))
    return picture


class WhichWindowsTests(unittest.TestCase):

    def test_chat_and_mail_applications_by_name_on_every_platform(self):
        config = Config()
        for application in ("Weixin.exe", "WeChat", "微信", "WeChat.app", "Slack", "Telegram Desktop",
                            "OUTLOOK.EXE", "olk.exe", "Thunderbird", "Mail", "Foxmail.exe", "DingTalk"):
            self.assertTrue(wanted(config, application, ""), application)
        for application in ("WINWORD.EXE", "Code.exe", "Preview", "Finder", "explorer.exe"):
            self.assertFalse(wanted(config, application, "A synthetic report"), application)

    def test_web_mail_chat_and_social_pages_by_their_tab_title(self):
        config = Config()
        for title in ("Inbox (3) - someone@example.com - Gmail", "Mail - Example Person - Outlook",
                      "QQ邮箱 - 收件箱", "网易免费邮箱 - 163邮箱", "Yahoo Mail", "Proton Mail",
                      "general - Example Team - Slack", "WhatsApp", "Home / X", "微博", "小红书 - 你的生活指南",
                      "Feed | LinkedIn"):
            self.assertTrue(wanted(config, "chrome.exe", title), title)
        self.assertFalse(wanted(config, "chrome.exe", "A synthetic recipe page - Example Site"))

    def test_the_lists_and_the_switch_are_settings(self):
        config = Config(listener_fast_applications="Example Chat\nAnother.exe", listener_fast_titles="example board")
        self.assertTrue(wanted(config, "Example Chat", ""))
        self.assertTrue(wanted(config, "another.exe", ""))
        self.assertFalse(wanted(config, "Weixin.exe", ""), "the list given replaces the defaults")
        self.assertTrue(wanted(config, "firefox.exe", "The Example Board - Firefox"))
        config["listener_fast_enabled"] = False
        self.assertFalse(wanted(config, "Example Chat", ""))


class FingerprintTests(unittest.TestCase):

    def test_the_same_picture_and_a_changed_one(self):
        one = fingerprint(stripes(20))
        self.assertTrue(same_picture(one, fingerprint(stripes(20))))
        self.assertTrue(same_picture(one, fingerprint(stripes(20).resize((360, 320)))), "the size alone")
        self.assertFalse(same_picture(one, fingerprint(stripes(7))))
        self.assertIsNone(fingerprint(None))
        self.assertFalse(same_picture(None, one), "no picture is never the same")


@patch("personalized_agent.listener.providers.fast_reading._on_battery", return_value=False)
class PaceTests(unittest.TestCase):

    def setUp(self):
        self.pace = Pace(Config())
        self.window = ("WeChat", "Test chat", 1)

    def test_read_when_changed_and_slower_while_unchanged(self, _battery):
        self.assertTrue(self.pace.looked(self.window, 7, 100))
        self.assertFalse(self.pace.looked(self.window, 7, 101))
        self.assertFalse(self.pace.looked(self.window, 7 ^ 0b11, 102), "two bits apart is the same")
        self.assertTrue(self.pace.looked(("WeChat", "Other chat", 1), 7 ^ 0b11, 103), "a new window")
        self.assertEqual(self.pace.interval(False, 104), 3.0)
        self.assertEqual(self.pace.interval(False, 113), 3.0)
        self.assertEqual(self.pace.interval(False, 133), 5.0)
        self.assertEqual(self.pace.interval(True, 133), 3.0, "typing keeps every 3 s")
        self.assertTrue(self.pace.read_now(False, True, 133))
        self.assertFalse(self.pace.read_now(False, False, 133))

    def test_a_budget_of_one_core_over_half_a_minute(self, _battery):
        self.pace.took(100, 7.0)
        self.assertFalse(self.pace.over_budget(101), "7 s of 30 is within 25%")
        self.pace.took(101, 1.0)
        self.assertTrue(self.pace.over_budget(102))
        self.assertFalse(self.pace.read_now(True, True, 102))
        self.assertEqual(self.pace.held_by_budget, 1)
        self.assertEqual(self.pace.interval(True, 102), 5.0)
        self.assertFalse(self.pace.over_budget(131), "the old reads have left the half minute")

    def test_on_battery_never_more_often_than_its_floor(self, battery):
        battery.return_value = True
        self.assertEqual(self.pace.interval(True, 100), 5.0)
        self.pace.config["listener_fast_battery_seconds"] = 10
        self.assertEqual(self.pace.interval(True, 100), 10.0)

    def test_settings_are_bounded(self, _battery):
        self.pace.config.update(listener_fast_interval_seconds=0.1, listener_fast_cpu_percent=500)
        self.assertEqual((self.pace.fastest(), self.pace.budget_share()), (3.0, 1.0), "never faster than 3 s")
        self.pace.config["listener_fast_interval_seconds"] = "not a number"
        self.assertEqual(self.pace.fastest(), 3.0)

    def test_forgetting_makes_the_next_look_a_change(self, _battery):
        self.pace.looked(self.window, 7, 100)
        self.pace.forget()
        self.assertTrue(self.pace.looked(self.window, 7, 101))


class BatteryTests(unittest.TestCase):

    def test_no_battery_or_no_psutil_is_not_on_battery(self):
        with patch("psutil.sensors_battery", return_value=None):
            self.assertFalse(fast_reading._on_battery())
        with patch("psutil.sensors_battery", side_effect=RuntimeError("no sensors")):
            self.assertFalse(fast_reading._on_battery())


if __name__ == "__main__":
    unittest.main()

"""THE WHOLE SCREEN ON THE MAC AND ON LINUX (the user, 2026-11-08: "mac不要涂灰，我要的效果就是不带chat的截图";
"linux也要做"; "linux上实在做不到就带窗口截吧"). Everything here is made up; the systems' own calls are
stand-ins."""

from __future__ import annotations

import io
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from PIL import Image

from personalized_agent.platform_layer import screen_picture as sp


class TheMacsTests(unittest.TestCase):

    INFOS = [  # from the front, as CGWindowListCopyWindowInfo gives them
        {"kCGWindowNumber": 11, "kCGWindowOwnerPID": 4242, "kCGWindowOwnerName": "Python",
         "kCGWindowName": "Chat", "kCGWindowLayer": 3,
         "kCGWindowBounds": {"X": 1500, "Y": 600, "Width": 380, "Height": 300}},
        {"kCGWindowNumber": 12, "kCGWindowOwnerPID": 77, "kCGWindowOwnerName": "KeePassXC",
         "kCGWindowName": "Vault", "kCGWindowLayer": 0,
         "kCGWindowBounds": {"X": 100, "Y": 100, "Width": 600, "Height": 400}},
        {"kCGWindowNumber": 13, "kCGWindowOwnerPID": 88, "kCGWindowOwnerName": "Safari",
         "kCGWindowName": "Tides", "kCGWindowLayer": 0,
         "kCGWindowBounds": {"X": 0, "Y": 25, "Width": 1920, "Height": 1055}},
        {"kCGWindowNumber": 14, "kCGWindowOwnerPID": 1, "kCGWindowOwnerName": "Dock",
         "kCGWindowName": "Desktop", "kCGWindowLayer": -2147483624,
         "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1920, "Height": 1080}}]

    def test_its_own_and_those_never_read_left_out_not_painted(self) -> None:
        windows = sp.mac_windows(self.INFOS, 4242)
        self.assertEqual([one["ours"] for one in windows], [True, False, False, False])
        self.assertEqual(windows[2]["box"], (0, 25, 1920, 1080))
        kept = sp.kept_in_picture(windows, hidden=lambda app, title: "keepass" in app.lower())
        self.assertEqual(kept, [13, 14], "the chat and the vault are not in it at all")
        self.assertEqual(sp.the_window_meant(windows, "Tides")["id"], 13)
        self.assertEqual(sp.the_window_meant(windows, "")["id"], 12, "else the front one")

    def test_the_display_its_window_is_on(self) -> None:
        displays = [(0, 0, 1920, 1080), (1920, 0, 4480, 1440)]
        self.assertEqual(sp.display_of((2000, 100, 2800, 900), displays), displays[1])
        self.assertEqual(sp.display_of((100, 100, 300, 300), displays), displays[0])
        self.assertEqual(sp.display_of(None, displays), displays[0])
        self.assertIsNone(sp.display_of(None, []))

    def test_taken_of_the_windows_kept_only(self) -> None:
        asked = {}
        quartz = types.ModuleType("Quartz")
        quartz.CGPreflightScreenCaptureAccess = lambda: True
        quartz.kCGWindowListOptionOnScreenOnly, quartz.kCGNullWindowID = 1, 0
        quartz.kCGWindowImageDefault = 0
        quartz.CGWindowListCopyWindowInfo = lambda *a: self.INFOS
        quartz.CGGetActiveDisplayList = lambda *a: (0, [1], 1)
        quartz.CGDisplayBounds = lambda one: SimpleNamespace(
            origin=SimpleNamespace(x=0, y=0), size=SimpleNamespace(width=1920, height=1080))
        quartz.CGRectMake = lambda *a: a

        def made(rect, ids, how):
            asked.update(rect=rect, ids=list(ids))
            return "picture"
        quartz.CGWindowListCreateImageFromArray = made
        png = io.BytesIO()
        Image.new("RGB", (1920, 1080), (10, 20, 30)).save(png, "PNG")

        class Data(bytearray):
            @classmethod
            def data(cls):
                return cls()
        quartz.CGImageDestinationCreateWithData = lambda data, *a: data
        quartz.CGImageDestinationAddImage = lambda data, *a: data.extend(png.getvalue())
        quartz.CGImageDestinationFinalize = lambda data: True
        foundation = types.ModuleType("Foundation")
        foundation.NSMutableData = Data
        from personalized_agent.platform_layer import macos
        provider = macos.MacActivityProvider.__new__(macos.MacActivityProvider)
        with mock.patch.dict(sys.modules, {"Quartz": quartz, "Foundation": foundation}), \
                mock.patch("os.getpid", return_value=4242):
            image = provider.user_screen_image(
                title="Tides", hidden=lambda app, title: "keepass" in app.lower())
        self.assertEqual(asked["ids"], [13, 14])
        self.assertEqual(asked["rect"], (0, 0, 1920, 1080))
        self.assertEqual(image.getpixel((5, 5)), (10, 20, 30), "nothing painted over")


class LinuxsTests(unittest.TestCase):

    def test_what_x_says_read(self) -> None:
        from personalized_agent.platform_layer import linux
        self.assertEqual(linux.stacked_ids(
            "_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x1a00003, 0x2c00007, 0x3e00001"),
            ["0x1a00003", "0x2c00007", "0x3e00001"])
        self.assertEqual(linux.props_of('_NET_WM_PID(CARDINAL) = 4242\n'
                                        '_NET_WM_NAME(UTF8_STRING) = "Tides - Firefox"'),
                         (4242, "Tides - Firefox"))
        self.assertEqual(linux.monitors_of("Monitors: 2\n 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1\n"
                                           " 1: +HDMI-1 2560/597x1440/336+1920+0  HDMI-1"),
                         [(0, 0, 1920, 1080), (1920, 0, 4480, 1440)])

    def test_the_screen_with_its_own_windows_as_they_are_and_those_never_read_grey(self) -> None:
        from personalized_agent.platform_layer import linux
        provider = linux.LinuxActivityProvider.__new__(linux.LinuxActivityProvider)
        provider._xdotool, provider._xprop = "xdotool", "xprop"
        geometry = {"0x1": "X=0\nY=0\nWIDTH=400\nHEIGHT=300",
                    "0x2": "X=100\nY=0\nWIDTH=100\nHEIGHT=100",
                    "0x3": "X=10\nY=10\nWIDTH=50\nHEIGHT=50"}
        props = {"0x1": '_NET_WM_PID(CARDINAL) = 88\n_NET_WM_NAME(UTF8_STRING) = "Tides"',
                 "0x2": '_NET_WM_PID(CARDINAL) = 77\n_NET_WM_NAME(UTF8_STRING) = "Vault"',
                 "0x3": '_NET_WM_PID(CARDINAL) = 4242\n_NET_WM_NAME(UTF8_STRING) = "Chat"'}

        def run(argv, **_k):
            if argv[:2] == ["xprop", "-root"]:
                out = "_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x1, 0x2, 0x3"
            elif argv[0] == "xdotool":
                out = geometry[argv[-1]]
            else:
                out = props[argv[2]]
            return SimpleNamespace(stdout=out)
        names = {88: "firefox", 77: "keepassxc", 4242: "python3"}
        with mock.patch.dict("os.environ", {"DISPLAY": ":0"}), \
                mock.patch.object(linux.children, "run_short", side_effect=run), \
                mock.patch.object(linux, "which", return_value=None), \
                mock.patch("psutil.Process", side_effect=lambda pid: SimpleNamespace(
                    name=lambda: names[pid])), \
                mock.patch("os.getpid", return_value=4242), \
                mock.patch("PIL.ImageGrab.grab",
                           return_value=Image.new("RGB", (400, 300), (255, 255, 255))):
            image = provider.user_screen_image(title="Tides",
                                               hidden=lambda app, title: "keepass" in app.lower())
        self.assertEqual(image.getpixel((30, 30)), (255, 255, 255), "the chat, as it is")
        self.assertEqual(image.getpixel((150, 50)), sp.BLANKED, "a window never read: grey")
        self.assertEqual(image.getpixel((300, 200)), (255, 255, 255))

    def test_not_on_wayland_nor_without_x_tools(self) -> None:
        from personalized_agent.platform_layer import linux
        provider = linux.LinuxActivityProvider.__new__(linux.LinuxActivityProvider)
        provider._xdotool, provider._xprop = None, "xprop"
        self.assertIsNone(provider.user_screen_image())
        provider._xdotool = "xdotool"
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(provider.user_screen_image())


if __name__ == "__main__":
    unittest.main()

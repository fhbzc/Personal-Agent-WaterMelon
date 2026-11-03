"""A BUTTON FELT WHEN PRESSED (the user, 2026-11-02: "点击之后要有一个触感（左键
有反馈，虽然不是完全选择，但是要有个反馈）"): pressed, a shade darker at once;
dragged off while held, as it was - and nothing done when let go there; let go
over it, done. The notification's own hover colours are left as they are."""

from __future__ import annotations

import unittest

import support  # noqa: F401

from personalized_agent.ui.on_top import on_release, pressed_shade

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
    _root.destroy()
    HAS_TK = True
except Exception:                                        # pragma: no cover - no display
    HAS_TK = False


@unittest.skipUnless(HAS_TK, "no display for Tk")
class PressedFeelTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.geometry("+0+0")
        self.addCleanup(self.root.destroy)
        self.done = []
        self.face = tk.Label(self.root, text="Got it", bg="#2f6fd6", padx=20, pady=10)
        self.face.pack()
        # As the notification's buttons are: a colour of their own on hover.
        self.face.bind("<Enter>", lambda _e: self.face.configure(bg="#4a86e8"))
        self.face.bind("<Leave>", lambda _e: self.face.configure(bg="#2f6fd6"))
        on_release(self.face, lambda: self.done.append(True))
        self.root.update()

    def bg(self) -> str:
        return str(self.face.cget("bg")).lower()

    def fire(self, sequence: str, x: int = 5, y: int = 5, **more) -> None:
        self.face.event_generate(sequence, x=x, y=y, **more)
        self.root.update()

    def test_darker_at_once_when_pressed_back_and_done_when_let_go(self) -> None:
        self.fire("<Enter>")
        hover = self.bg()
        self.fire("<ButtonPress-1>")
        self.assertEqual(self.bg(), pressed_shade(self.face, hover).lower(), "felt at once")
        self.assertEqual(self.done, [], "not done yet: only pressed")
        self.fire("<ButtonRelease-1>")
        self.assertEqual(self.bg(), hover, "back as it was")
        self.assertEqual(self.done, [True])

    def test_dragged_off_it_shows_as_it_was_and_does_nothing(self) -> None:
        self.fire("<Enter>")
        self.fire("<ButtonPress-1>")
        self.fire("<Leave>")
        self.assertEqual(self.bg(), "#2f6fd6", "its own leave colour, not the pressed one")
        self.fire("<ButtonRelease-1>", x=-40, y=-40)
        self.assertEqual(self.bg(), "#2f6fd6", "not left pressed, nor in its hover colour")
        self.assertEqual(self.done, [])

    def test_dragged_back_on_it_shows_pressed_again(self) -> None:
        self.fire("<Enter>")
        hover = self.bg()
        self.fire("<ButtonPress-1>")
        self.fire("<Leave>")
        self.fire("<Enter>")
        self.assertEqual(self.bg(), pressed_shade(self.face, hover).lower())
        self.fire("<ButtonRelease-1>")
        self.assertEqual(self.done, [True])

    def test_lighter_on_a_dark_one(self) -> None:
        dark = pressed_shade(self.face, "#1c232d")
        light = pressed_shade(self.face, "#f5f8fc")
        self.assertGreater(int(dark[1:3], 16), 0x1c, "a dark one goes lighter")
        self.assertLess(int(light[1:3], 16), 0xf5, "a light one goes darker")


if __name__ == "__main__":
    unittest.main()

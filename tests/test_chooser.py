"""The folder and application dialogs open on the thread that owns the app's
windows when the desktop window is running - a second Tk on another thread
failed without a word - and on a thread of their own when it is not."""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from personalized_agent.common import chooser


class _Parent:
    def __init__(self) -> None:
        self.destroyed = False
        self.topmost = False

    def attributes(self, name, value) -> None:
        self.topmost = bool(value)

    def withdraw(self) -> None:
        pass

    def destroy(self) -> None:
        self.destroyed = True


class TheDialogOpensOnTheWindowsThreadTests(unittest.TestCase):

    def tearDown(self) -> None:
        chooser._HOST = None

    def test_with_the_window_running_it_is_asked_for_on_its_thread(self) -> None:
        submitted, threads, made = [], [], []

        def submit(fn) -> None:
            submitted.append(fn)
            # The window's thread runs it - here, another thread, as Tk's is.
            worker = threading.Thread(target=fn)
            threads.append(worker)
            worker.start()

        def toplevel(root):
            made.append(_Parent())
            return made[-1]

        chooser.dialogs_on(object(), submit)
        with mock.patch("tkinter.Toplevel", side_effect=toplevel):
            chosen = chooser._show(lambda parent: "C:/work/project", "directory")
        self.assertEqual(chosen, "C:/work/project")
        self.assertEqual(len(submitted), 1)
        self.assertTrue(made[0].topmost, "the dialog opens in front")
        self.assertTrue(made[0].destroyed)

    def test_a_dialog_that_fails_says_why(self) -> None:
        chooser.dialogs_on(object(), lambda fn: fn())

        def broken(parent):
            raise RuntimeError("no display")

        with mock.patch("tkinter.Toplevel", side_effect=lambda root: _Parent()):
            chosen = chooser._show(broken, "directory")
        self.assertIsInstance(chosen, RuntimeError)

    def test_choose_directory_reports_what_was_chosen(self) -> None:
        chooser.dialogs_on(object(), lambda fn: fn())
        with mock.patch("tkinter.Toplevel", side_effect=lambda root: _Parent()), \
                mock.patch("tkinter.filedialog.askdirectory",
                           return_value="C:/Users/me/Desktop/paper"), \
                mock.patch.object(chooser, "available", return_value=True):
            said = chooser.choose_directory("The project's folder")
        self.assertTrue(said["available"])
        self.assertFalse(said["cancelled"])
        self.assertTrue(said["selected"].replace("\\", "/").endswith("Desktop/paper"))

    def test_cancelled_is_cancelled(self) -> None:
        chooser.dialogs_on(object(), lambda fn: fn())
        with mock.patch("tkinter.Toplevel", side_effect=lambda root: _Parent()), \
                mock.patch("tkinter.filedialog.askdirectory", return_value=""), \
                mock.patch.object(chooser, "available", return_value=True):
            said = chooser.choose_directory()
        self.assertEqual((said["selected"], said["cancelled"]), (None, True))

    def test_getting_the_dialog_ready_opens_nothing_under_the_tests(self) -> None:
        with mock.patch("tkinter.filedialog.askdirectory") as asked:
            chooser.warm_up(object())
        asked.assert_not_called()

    def test_getting_the_dialog_ready_is_windows_only(self) -> None:
        with mock.patch.object(chooser.sys, "platform", "darwin"),                 mock.patch.object(chooser, "available", return_value=True),                 mock.patch("tkinter.filedialog.askdirectory") as asked:
            chooser.warm_up(object())
        asked.assert_not_called()

    def test_the_desktop_window_registers_itself(self) -> None:
        import inspect

        from personalized_agent.ui import desktop
        self.assertIn("chooser.dialogs_on(self.root", inspect.getsource(desktop))
        # After the agent is up (say_hello), not while it starts.
        self.assertIn("chooser.warm_up(self.root)",
                      inspect.getsource(desktop.DesktopAgent.say_hello))


if __name__ == "__main__":
    unittest.main()

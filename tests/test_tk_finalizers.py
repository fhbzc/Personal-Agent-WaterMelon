"""What Python lets go of is cleaned up in Tcl by Tk's thread in its own time, never from within a window
procedure (2026-11-17: "Fatal Python error: PyEval_RestoreThread ... the current Python thread state is NULL")."""

from __future__ import annotations

import gc
import tkinter as tk
import tkinter.font
import unittest

import support  # noqa: F401

from personalized_agent.ui import tk_finalizers as tf


class KeptForTksOwnTimeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.assertTrue(tf.install(self.root))
        self.addCleanup(self._end)

    def _end(self) -> None:
        tf.uninstall()
        tf.done_now()
        self.root.destroy()

    def test_a_variable_an_image_and_a_font_let_go_of_touch_no_tcl_until_tk_does_it(self) -> None:
        variable = tk.StringVar(self.root, value="x")
        variable.trace_add("write", lambda *_a: None)
        image = tk.PhotoImage(master=self.root, width=2, height=2)
        font = tkinter.font.Font(root=self.root, size=9)
        names = (str(variable), image.name, font.name)
        before = tf.kept()
        del variable, image, font
        gc.collect()
        self.assertEqual(tf.kept(), before + 3, "kept, none done here")
        self.assertTrue(self.root.getboolean(self.root.tk.call("info", "exists", names[0])))
        self.assertIn(names[1], self.root.tk.call("image", "names"))
        self.assertIn(names[2], self.root.tk.call("font", "names"))
        self.assertGreaterEqual(tf.done_now(), 3)
        self.assertFalse(self.root.getboolean(self.root.tk.call("info", "exists", names[0])))
        self.assertNotIn(names[1], self.root.tk.call("image", "names"))
        self.assertNotIn(names[2], self.root.tk.call("font", "names"))

    def test_pils_pictures_too(self) -> None:
        from PIL import Image, ImageTk
        picture = ImageTk.PhotoImage(Image.new("RGB", (2, 2)), master=self.root)
        name = str(picture)
        del picture
        gc.collect()
        self.assertIn(name, self.root.tk.call("image", "names"), "not yet")
        tf.done_now()
        self.assertNotIn(name, self.root.tk.call("image", "names"))

    def test_tks_own_thread_does_them_from_its_callback(self) -> None:
        image = tk.PhotoImage(master=self.root, width=2, height=2)
        name = image.name
        del image
        gc.collect()
        self.root.after(tf.EVERY_MS + 300, self.root.quit)
        self.root.mainloop()
        self.assertNotIn(name, self.root.tk.call("image", "names"))

    def test_once_and_back(self) -> None:
        self.assertFalse(tf.install(self.root), "once")
        tf.uninstall()
        self.assertIsNot(tk.Image.__dict__.get("__del__"), tf._image_let_go)
        self.assertTrue(tf.install(self.root))


if __name__ == "__main__":
    unittest.main()

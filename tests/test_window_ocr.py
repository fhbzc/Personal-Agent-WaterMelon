"""A window's words read by Apple Vision, accurate (the user, 2026-10-26: Vision,
not Tesseract) - in memory, bounded; each line with its box, from the top
left in pixels, and its confidence. Vision is not on the machines these run
on: a stand-in records what it is asked and answers as Vision does."""

import io
import sys
import time
import types
import unittest
from unittest.mock import patch

from personalized_agent.platform_layer.window_ocr import LocalWindowOcr


def _png(width=200, height=100):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


class _Box:
    def __init__(self, x, y, w, h):
        self.origin = types.SimpleNamespace(x=x, y=y)
        self.size = types.SimpleNamespace(width=w, height=h)


class _Observation:
    def __init__(self, text, confidence, box):
        self._text, self._confidence, self._box = text, confidence, box

    def topCandidates_(self, _count):
        said = types.SimpleNamespace(string=lambda: self._text,
                                     confidence=lambda: self._confidence)
        return [said]

    def boundingBox(self):
        return self._box


def _vision(lines, ok=True, slow=0.0):
    """A stand-in Vision and Foundation: the request's settings recorded."""
    asked = {}

    class Request:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def setRecognitionLevel_(self, level):
            asked["level"] = level

        def setUsesLanguageCorrection_(self, value):
            asked["correction"] = value

        def setRecognitionLanguages_(self, languages):
            asked["languages"] = list(languages)

        def results(self):
            return lines

    class Handler:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithData_options_(self, data, _options):
            asked["data"] = data
            return self

        def performRequests_error_(self, _requests, _error):
            if slow:
                time.sleep(slow)
            return (ok, None if ok else "a stand-in failure")

    vision = types.SimpleNamespace(VNRecognizeTextRequest=Request, VNImageRequestHandler=Handler)
    foundation = types.SimpleNamespace(NSData=types.SimpleNamespace(
        dataWithBytes_length_=lambda raw, size: raw[:size]))
    return {"Vision": vision, "Foundation": foundation}, asked


class LocalWindowOcrTests(unittest.TestCase):

    def test_accurate_chinese_and_english_each_line_with_its_box(self):
        lines = [_Observation("你好 Ada", 0.9, _Box(0.1, 0.7, 0.5, 0.2)),
                 _Observation("下周三可以", 0.75, _Box(0.5, 0.1, 0.4, 0.2))]
        modules, asked = _vision(lines)
        png = _png()
        with patch.dict(sys.modules, modules):
            engine = LocalWindowOcr()
            layout = engine.read_layout(png)
            text = engine.read(png)
        self.assertEqual((asked["level"], asked["correction"], asked["languages"]),
                         (0, True, ["zh-Hans", "en-US"]), "Vision's accurate level, said outright")
        self.assertEqual(asked["data"], png, "the picture in memory, nothing on disk")
        self.assertEqual(text, "你好 Ada\n下周三可以")
        self.assertEqual((layout["width"], layout["height"]), (200, 100))
        first, second = layout["words"]
        # From Vision's bottom left, in fractions, to the top left, in pixels.
        self.assertEqual(first["bbox"], [20, 10, 100, 20])
        self.assertEqual(second["bbox"], [100, 70, 80, 20])
        self.assertEqual((first["confidence"], second["confidence"]), (90.0, 75.0))
        self.assertEqual(engine.method, "macos_vision_ocr")
        status = engine.status()
        self.assertTrue(status["apple_vision_enabled"])
        self.assertEqual((status["engine"], status["level"]), ("apple_vision", "accurate"))

    def test_without_vision_it_says_so_and_reads_nothing(self):
        with patch.dict(sys.modules, {"Vision": None}):
            engine = LocalWindowOcr()
            with self.assertRaisesRegex(RuntimeError, "Apple Vision is not available"):
                engine.read(_png())
            self.assertFalse(engine.status()["available"])
            self.assertEqual(engine.languages(), [])

    def test_a_failure_said_and_a_later_success_clears_it(self):
        modules, _asked = _vision([], ok=False)
        with patch.dict(sys.modules, modules):
            engine = LocalWindowOcr()
            with self.assertRaisesRegex(RuntimeError, "could not read the window"):
                engine.read(_png())
            self.assertIn("could not read", engine.status()["last_error"])
        modules, _asked = _vision([_Observation("Recovered", 1.0, _Box(0, 0, 1, 1))])
        with patch.dict(sys.modules, modules):
            self.assertEqual(engine.read(_png()), "Recovered")
        self.assertEqual(engine.status()["last_error"], "")

    def test_a_reading_has_a_deadline(self):
        modules, _asked = _vision([], slow=1.0)
        with patch.dict(sys.modules, modules), patch.object(LocalWindowOcr, "TIMEOUT", 0.2):
            with self.assertRaisesRegex(RuntimeError, "timed out after"):
                LocalWindowOcr().read(_png())

    def test_the_picture_reader_stays_on_vision(self):
        # Pictures and documents are read by Vision as before; WeChat too.
        from personalized_agent.platform_layer import ocr
        self.assertEqual(ocr.MacVisionOcr.name, "Apple Vision")
        self.assertFalse(hasattr(ocr, "MacLocalOcr"))


if __name__ == "__main__":
    unittest.main()

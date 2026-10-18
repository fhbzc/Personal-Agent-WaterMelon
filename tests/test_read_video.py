"""Work's read_video: a video read on this computer into frames and what is
said, written only into the work's own folder."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import av  # noqa: F401
    HAVE_AV = True
except Exception:                                  # pragma: no cover
    HAVE_AV = False


def _make_video(path: Path, seconds: int = 4) -> None:
    """A short video of plain coloured frames, one colour a second."""
    import av
    from PIL import Image
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=5)
        stream.width, stream.height = 160, 120
        stream.pix_fmt = "yuv420p"
        colours = [(200, 40, 40), (40, 200, 40), (40, 40, 200), (200, 200, 40)]
        for n in range(seconds * 5):
            image = Image.new("RGB", (160, 120), colours[(n // 5) % len(colours)])
            for packet in stream.encode(av.VideoFrame.from_image(image)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


@unittest.skipUnless(HAVE_AV, "PyAV is not installed")
class ReadVideoTests(unittest.TestCase):

    def setUp(self) -> None:
        from support import TemporaryAgent
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.mine = Path(tempfile.mkdtemp())
        self.clip = self.mine / "garden tour.mp4"
        _make_video(self.clip)
        unit = {"id": "unit_video", "name": "Video work", "kind": "task"}
        self.work = Path(self.app.work.folder_of(unit))
        self.runtime = self.app.work._runtime(unit, self.work)
        # The user's own folder, readable as the user's home is.
        self.runtime.policy.readable_paths = [*self.runtime.policy.readable_paths,
                                              self.mine.resolve()]

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.mine, ignore_errors=True)
        self.agent.cleanup()

    def _tools(self):
        from personalized_agent.agent.work.tools import WorkTools
        return WorkTools(self.runtime)

    def test_frames_and_speech_kept_in_the_works_own_folder(self) -> None:
        from personalized_agent.runtime import video
        heard = {"language": "en", "lines": ["[00:01] Here is the garden."]}
        with mock.patch.object(video, "speech_of", return_value=heard), \
                mock.patch("personalized_agent.runtime.documents.ocr_file", return_value=""):
            said = self._tools().read_video({"path": str(self.clip), "frames": 4})
        self.assertNotIn("error", said, said)
        folder = Path(said["folder"])
        self.assertTrue(str(folder).startswith(str(self.work / "videos")), folder)
        frames = sorted(folder.glob("frame_*.jpg"))
        self.assertEqual(len(frames), 4)
        self.assertEqual((folder / "transcript.txt").read_text(encoding="utf-8"),
                         "[00:01] Here is the garden.")
        self.assertIn("Here is the garden.", said["text"])
        self.assertIn("FRAME at 00:00", said["text"])
        self.assertEqual(len(said["_pictures"]), 4)
        # Nothing written beside the user's own video.
        self.assertEqual([one.name for one in self.mine.iterdir()], ["garden tour.mp4"])

    def test_read_again_from_what_was_kept(self) -> None:
        from personalized_agent.runtime import video
        with mock.patch.object(video, "speech_of",
                               return_value={"language": "en", "lines": []}) as asked, \
                mock.patch("personalized_agent.runtime.documents.ocr_file", return_value=""):
            tools = self._tools()
            tools.read_video({"path": str(self.clip)})
            again = tools.read_video({"path": str(self.clip)})
        self.assertEqual(asked.call_count, 1)
        self.assertTrue(again["from_copy"])

    def test_a_frame_showing_a_password_is_not_kept_or_shown(self) -> None:
        from personalized_agent.runtime import video
        with mock.patch("personalized_agent.runtime.documents.ocr_file",
                        return_value="Password: tulip-4471"):
            said = self._tools().read_video({"path": str(self.clip), "frames": 2,
                                             "speech": False})
        self.assertNotIn("_pictures", said)
        self.assertNotIn("tulip-4471", said["text"])
        self.assertEqual(list(Path(said["folder"]).glob("frame_*.jpg")), [])

    def test_read_file_watches_a_video(self) -> None:
        with mock.patch("personalized_agent.runtime.documents.ocr_file", return_value=""):
            said = self._tools().read_file({"path": str(self.clip)})
        self.assertEqual(said.get("kind"), "video")

    def test_offered_as_a_tool(self) -> None:
        names = [one.name for one in self._tools().specs(writing=False)]
        self.assertIn("read_video", names)


class TheSpeechModelIsLetGoTests(unittest.TestCase):
    """Held only while used and for LINGER_SECONDS after, as the embedding
    model is - not for as long as the agent runs."""

    def setUp(self) -> None:
        from personalized_agent.runtime import video
        self.video = video
        video._model = None
        video._holders = 0

    def tearDown(self) -> None:
        self.video._model = None
        self.video._holders = 0

    def test_released_when_idle_but_not_while_used(self) -> None:
        import sys
        import time
        import types
        video = self.video
        fake = types.ModuleType("faster_whisper")
        fake.WhisperModel = lambda *a, **k: object()
        with mock.patch.dict(sys.modules, {"faster_whisper": fake}),                 mock.patch.object(video, "LINGER_SECONDS", 0.2):
            video._speech_model()
            self.assertTrue(video.model_loaded())
            time.sleep(0.4)
            self.assertTrue(video.model_loaded(), "let go while still in use")
            video._done_with_model()
            self.assertTrue(video.model_loaded())
            time.sleep(0.6)
            self.assertFalse(video.model_loaded(), "held after it was done with")

    def test_half_the_processor_at_most(self) -> None:
        with mock.patch("os.cpu_count", return_value=8):
            self.assertEqual(self.video._threads(), 4)
        with mock.patch("os.cpu_count", return_value=1):
            self.assertEqual(self.video._threads(), 1)


if __name__ == "__main__":
    unittest.main()

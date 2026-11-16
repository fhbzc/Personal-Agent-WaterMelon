"""The sentence model carried in the installer (the user, 2026-11-16: "把这个打包进安装包").

A copy that cannot reach Hugging Face would otherwise compare words instead,
quietly: the build fetches the pinned model's files, checks each against its
SHA-256, and puts them where the packaged worker reads them before it downloads.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT  # noqa: F401 - puts the repository on the path

from build import common
from build.common import BuildError
from personalized_agent.heartbeat.states import embed_worker as worker

MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def lay_out(root: str, missing: str = "") -> None:
    """The pinned model's files under ``root`` as the build lays them out, with made-up content."""
    folder = worker.bundled_folder(MODEL, root)
    for name in worker.BUNDLED_FILES[MODEL]:
        if name == missing:
            continue
        path = os.path.join(folder, worker.bundled_name(name))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"not the model")


class TheWorkerReadsWhatIsCarriedTests(unittest.TestCase):
    def test_the_carried_files_are_found_side_by_side(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            lay_out(root)
            found = worker.bundled(MODEL, root)
            self.assertEqual(set(found), {worker.ONNX_FILE, worker.TOKENISER_FILE})
            for path in found.values():
                self.assertTrue(os.path.isfile(path))
                self.assertEqual(os.path.dirname(path), worker.bundled_folder(MODEL, root))

    def test_the_path_stays_short(self) -> None:
        # Under the usual install folder, well inside Windows' 260 characters
        # (the first real build stopped at 271 with the revision in the path).
        install = r"D:\Applications\for.someone.with.a.long.name\Local\Programs\PersonalAgentWaterMelon\_internal"
        for name in worker.BUNDLED_FILES[MODEL]:
            path = os.path.join(worker.bundled_folder(MODEL, install), worker.bundled_name(name))
            self.assertLess(len(path), 160, path)

    def test_no_two_carried_files_share_a_name(self) -> None:
        for files in worker.BUNDLED_FILES.values():
            names = [worker.bundled_name(name) for name in files]
            self.assertEqual(len(names), len(set(names)))

    def test_a_missing_file_means_it_is_downloaded(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            lay_out(root, missing=worker.ONNX_FILE)
            self.assertIsNone(worker.bundled(MODEL, root))

    def test_run_from_source_nothing_is_carried(self) -> None:
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertIsNone(worker.bundled_root())
        self.assertIsNone(worker.bundled(MODEL))

    def test_another_model_is_never_looked_for(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(worker.bundled("someone/another-model", root))

    def test_every_carried_model_is_pinned(self) -> None:
        self.assertLessEqual(set(worker.BUNDLED_FILES), set(worker.REVISIONS))
        for files in worker.BUNDLED_FILES.values():
            self.assertEqual(set(files), {worker.ONNX_FILE, worker.TOKENISER_FILE})
            for digest in files.values():
                self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_a_damaged_copy_is_downloaded_instead(self) -> None:
        opened = []

        def fake_open(tokeniser, model):
            opened.append((tokeniser, model))
            if len(opened) == 1:
                raise RuntimeError("damaged")
            return "tokeniser", "session"

        downloads = []

        class FakeHub:
            @staticmethod
            def hf_hub_download(model, name, revision=None):
                downloads.append((model, name, revision))
                return f"cache/{name}"

        real_open, real_hub = worker._open, sys.modules.get("huggingface_hub")
        worker._open = fake_open
        sys.modules["huggingface_hub"] = FakeHub
        try:
            with tempfile.TemporaryDirectory() as root:
                lay_out(root)
                self.assertEqual(worker.load(MODEL, root), ("tokeniser", "session"))
        finally:
            worker._open = real_open
            if real_hub is None:
                sys.modules.pop("huggingface_hub", None)
            else:
                sys.modules["huggingface_hub"] = real_hub
        self.assertEqual(len(opened), 2)
        self.assertEqual({name for _model, name, _rev in downloads},
                         {worker.ONNX_FILE, worker.TOKENISER_FILE})
        self.assertTrue(all(rev == worker.REVISIONS[MODEL] for _m, _n, rev in downloads))


class TheBuildCarriesItTests(unittest.TestCase):
    def test_the_installer_puts_the_files_where_the_worker_looks(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "models"
            lay_out(str(root))
            args = common.model_data_arguments(";", root)
            pairs = [args[index + 1] for index in range(0, len(args), 2)]
            self.assertTrue(all(flag == "--add-data" for flag in args[0::2]))
            # Unpacked under the application's own folder, each file lands
            # where bundled() finds it.
            unpacked = Path(work) / "_internal"
            for pair in pairs:
                source, destination = pair.rsplit(";", 1)
                target = unpacked.joinpath(*destination.split("/")) / Path(source).name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(Path(source).read_bytes())
            self.assertIsNotNone(worker.bundled(MODEL, str(unpacked)))

    def test_a_build_without_the_model_stops(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "models"
            lay_out(str(root), missing=worker.TOKENISER_FILE)
            with self.assertRaises(BuildError):
                common.model_data_arguments(";", root)

    def test_a_file_that_does_not_match_its_sha256_stops_the_build(self) -> None:
        fetched = []

        def fetch(model, name, revision, folder):
            fetched.append((model, name, revision))
            path = Path(folder).joinpath(*name.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"something else")

        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(BuildError):
                common.prepare_models(Path(work) / "models", fetch=fetch)
        self.assertEqual(fetched[0][2], worker.REVISIONS[fetched[0][0]])

    def test_a_file_that_matches_is_not_fetched_again(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "models"
            lay_out(str(root))
            real = dict(worker.BUNDLED_FILES[MODEL])
            # The made-up content's own digest stands in for the real one.
            digest = common._sha256(next(path for path, _n, _d in common.model_files(root)))
            fetched = []
            original = common._embed_worker

            def pinned_to_made_up():
                module = original()
                module.BUNDLED_FILES = {MODEL: {name: digest for name in real}}
                return module

            common._embed_worker = pinned_to_made_up
            try:
                common.prepare_models(root, fetch=lambda *a: fetched.append(a))
            finally:
                common._embed_worker = original
            self.assertEqual(fetched, [])

    def test_the_packaging_command_carries_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "models"
            lay_out(str(root))
            original = common.MODELS_ROOT
            real_default = common.model_data_arguments.__defaults__
            common.model_data_arguments.__defaults__ = (root,)
            try:
                command = common.pyinstaller_command(common.current_platform(), name="Test")
            finally:
                common.model_data_arguments.__defaults__ = real_default
                common.MODELS_ROOT = original
            self.assertTrue(any(str(root) in part for part in command))


if __name__ == "__main__":
    unittest.main()

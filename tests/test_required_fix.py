"""A REQUIRED FIX (platform_layer/updater.py REQUIRED_MARK; the user, 2026-11-15: "这个对所有按照0.3.5的用户会自动安装
（用户哪怕没有自动更新，补丁都是自动安装的)"): a release whose notes carry the mark is had whatever the user chose for
updates - Ask and Off too - made ready in the background, and told once; an ordinary one is as before."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import quiet_update as quiet
from personalized_agent.platform_layer import updater as up

ROOT = Path(__file__).resolve().parents[1]


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _release(tag: str, required: bool = False, prerelease: bool = False):
    files = [up.ASSET["windows"], quiet.QUIET_ASSET["windows"], up.SUMS]
    return {"tag": tag, "page": f"https://example.org/{tag}", "published": "", "notes": "New.",
            "required": required, "prerelease": prerelease, "draft": False,
            "files": {name: {"url": f"https://example.org/{name}", "size": 1} for name in files}}


class TheRequiredFixTests(unittest.TestCase):
    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.updater = up.Updater(_Config(), Path(work.name))
        self.updater.quit = lambda: None
        self.told, self.made_ready = [], []
        self.updater.notify = lambda title, message, **said: self.told.append((title, said.get("metadata")))
        self.listed = [_release("v9.0.0")]
        patches = [mock.patch.object(up, "how_installed", return_value="windows"),
                   mock.patch.object(up, "published_releases", side_effect=lambda pre=False: list(self.listed)),
                   mock.patch.object(quiet, "can_make_ready", return_value=True),
                   mock.patch.object(self.updater, "make_ready",
                                     side_effect=lambda r, k, shown=False: self.made_ready.append(r["tag"]))]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def test_the_mark_read_off_the_releases_notes(self) -> None:
        said = up._release_of({"tag_name": "v9.0.0", "body": f"Fixes.\n{up.REQUIRED_MARK}\n"})
        self.assertTrue(said["required"])
        self.assertNotIn(up.REQUIRED_MARK, said["notes"], "the mark is not shown with the notes")
        self.assertFalse(up._release_of({"tag_name": "v9.0.0", "body": "Fixes."})["required"])

    def test_had_under_ask_and_told_once(self) -> None:
        self.listed = [_release("v9.0.0", required=True)]
        self.updater.once()
        self.updater.once()                      # looked again (made ready is not kept by the stand-in)
        self.assertEqual(set(self.made_ready), {"v9.0.0"})
        self.assertEqual(len(self.told), 1, "told once")
        self.assertIn("required fix", self.told[0][0])

    def test_had_under_off_too_and_an_ordinary_one_is_not(self) -> None:
        self.updater.config["updates"] = "off"
        self.updater.once()
        self.assertEqual((self.made_ready, self.told), ([], []), "off: an ordinary release, nothing")
        self.listed = [_release("v9.0.0", required=True)]
        self.updater.once()
        self.assertEqual(self.made_ready, ["v9.0.0"])

    def test_the_newest_is_had_when_an_older_one_was_the_fix(self) -> None:
        self.updater.config["updates"] = "off"
        self.listed = [_release("v9.1.0"), _release("v9.0.0", required=True)]
        self.updater.once()
        self.assertEqual(self.made_ready, ["v9.1.0"], "it has the fix too")

    def test_a_pre_release_marked_is_never_had_by_itself(self) -> None:
        self.updater.config["updates"] = "off"
        self.listed = [_release("v9.0.0", required=True, prerelease=True)]
        self.updater.once()
        self.assertEqual(self.made_ready, [])

    def test_one_older_than_this_one_changes_nothing(self) -> None:
        self.updater.config["updates"] = "off"
        self.listed = [_release("v0.0.1", required=True)]
        self.updater.once()
        self.assertEqual(self.made_ready, [])

    def test_the_setting_says_so(self) -> None:
        schema = (ROOT / "personalized_agent" / "common" / "config" / "schema.py").read_text("utf-8")
        self.assertIn("A required fix is", schema)
        page = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text("utf-8")
        self.assertIn("only a required fix, which is had all the same", page)


if __name__ == "__main__":
    unittest.main()

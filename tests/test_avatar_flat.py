"""The flat watermelon (PR #9) as one more avatar to choose (the user, 2026-11-10: "把这个新的扁平
西瓜变成另外一个可选的avatar, 不要改之前的avatar。选中新的avatar之后切换"): listed beside the first one,
drawn by its own module, still called Watermelon - and the first one drawn as it was."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.agent.character import watermelon, watermelon_flat
from personalized_agent.agent.character.manager import CharacterManager, avatar_name, drawn_live

ROOT = Path(__file__).resolve().parents[1]
CHARACTERS = ROOT / "personalized_agent" / "resources" / "characters"


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Ask(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _characters(work: Path) -> CharacterManager:
    return CharacterManager(CHARACTERS, work)


class TwoToChooseTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_listed_beside_the_first_one_by_its_own_label(self) -> None:
        from personalized_agent.api.routes import build_routes
        said = build_routes(self.app)[("GET", "/api/settings")](_Ask())
        self.assertIn("watermelon", said["choices"]["characters"])
        self.assertIn("watermelon_flat", said["choices"]["characters"])
        labels = said["choice_labels"]["character"]
        self.assertEqual(labels["watermelon"], "Watermelon", "the first one as it was")
        self.assertEqual(labels["watermelon_flat"], "Flat watermelon")

    def test_still_called_watermelon(self) -> None:
        self.app.config.set("character", "watermelon_flat")
        self.assertEqual(self.app.avatar_name(), "Watermelon")
        self.assertEqual(avatar_name(_Config(character="watermelon_flat", agent_name="Pip"),
                                     self.app.characters), "Pip")

    def test_the_page_is_given_its_rig_and_layers(self) -> None:
        from personalized_agent.api.routes import build_routes
        said = build_routes(self.app)[("GET", "/api/characters")](_Ask(layers="1"))
        flat = next(c for c in said["characters"] if c["name"] == "watermelon_flat")
        self.assertEqual(flat["rig"]["variant"], "flat")
        self.assertTrue(flat["layers"]["leaves"].startswith("data:image/png;base64,"))
        first = next(c for c in said["characters"] if c["name"] == "watermelon")
        self.assertNotIn("variant", first["rig"], "the first one's rig untouched")


class DrawnByItsOwnTests(unittest.TestCase):

    def test_each_package_its_own_module(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            characters = _characters(Path(work))
            self.assertIs(drawn_live(characters.get("watermelon")), watermelon)
            self.assertIs(drawn_live(characters.get("watermelon_flat")), watermelon_flat)
        self.assertIsNone(drawn_live({"rig": {"kind": "pictures"}}))
        self.assertIsNone(drawn_live(None))

    def test_the_flat_one_draws_every_mood_and_is_not_the_first(self) -> None:
        flat = watermelon_flat.Watermelon(scale=1, package=CHARACTERS / "watermelon_flat")
        first = watermelon.Watermelon(scale=1, package=CHARACTERS / "watermelon")
        idle = flat.frame("idle").tobytes()
        for mood in ("hover", "talking", "thinking", "notification"):
            self.assertNotEqual(flat.frame(mood, t=0.5).tobytes(), idle, mood)
        self.assertNotEqual(idle, first.frame("idle").tobytes())

    def test_the_first_ones_manifest_as_it_was(self) -> None:
        manifest = json.loads((CHARACTERS / "watermelon" / "manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["rig"], watermelon.RIG)
        self.assertNotIn("label", manifest)

    def test_its_icon_its_own(self) -> None:
        import tempfile
        from personalized_agent.ui.tray import icon_image
        with tempfile.TemporaryDirectory() as work:
            characters = _characters(Path(work))
            first = icon_image(characters, "watermelon", 64)
            flat = icon_image(characters, "watermelon_flat", 64)
        self.assertEqual(first.size, flat.size)
        self.assertNotEqual(first.tobytes(), flat.tobytes())


class TheDesktopWearsItTests(unittest.TestCase):

    def test_chosen_worn_and_back(self) -> None:
        import tempfile
        from personalized_agent.ui.desktop import DesktopAgent
        with tempfile.TemporaryDirectory() as work:
            desktop = DesktopAgent.__new__(DesktopAgent)
            config = _Config(character="watermelon_flat")
            desktop.config, desktop._native_avatar, desktop._frames = config, None, {}
            desktop.size = 128
            desktop.app = SimpleNamespace(characters=_characters(Path(work)))
            desktop._wear_on_taskbar = lambda name: None
            desktop._load_character()
            self.assertIsInstance(desktop._rig, watermelon_flat.Watermelon)
            self.assertIs(desktop._rig_art, watermelon_flat, "its eyes from its own middle")
            config["character"] = "watermelon"
            desktop._load_character()
            self.assertIsInstance(desktop._rig, watermelon.Watermelon)
            self.assertIs(desktop._rig_art, watermelon)


if __name__ == "__main__":
    unittest.main()

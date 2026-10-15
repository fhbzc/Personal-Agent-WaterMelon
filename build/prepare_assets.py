"""Regenerate packaged application assets before a build (section 113).

The built-in character package is committed to the repository so a release can
be built without Pillow; this script recreates it when it is missing or when the
artwork changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BUILTIN_CHARACTERS = REPO_ROOT / "personalized_agent" / "resources" / "characters"
DEFAULT_PACKAGE = BUILTIN_CHARACTERS / "watermelon"


def ensure_assets(force: bool = False) -> Path:
    if DEFAULT_PACKAGE.joinpath("manifest.json").exists() and not force:
        return DEFAULT_PACKAGE
    from personalized_agent.agent.character.manager import (
        PIL_AVAILABLE,
        generate_default_package,
    )

    if not PIL_AVAILABLE:
        raise RuntimeError(
            "the built-in character package is missing and Pillow is not installed; "
            "install Pillow or restore personalized_agent/resources/characters/watermelon"
        )
    return generate_default_package(DEFAULT_PACKAGE)


#: The program's own icon, for each platform's packaging: Watermelon's face.
APP_ICONS = {"ico": REPO_ROOT / "build" / "windows" / "app.ico",
             "icns": REPO_ROOT / "build" / "macos" / "app.icns",
             "png": REPO_ROOT / "build" / "linux" / "app.png"}


def ensure_app_icons(force: bool = False) -> dict:
    """The program's icon - the installer's, the .exe's, the app's - from
    the default avatar, cut to the figure as the taskbar's is. Made when
    missing; the avatar a user chooses later is worn by the running windows
    and the tray (ui/desktop.py, ui/tray.py)."""
    if all(path.exists() for path in APP_ICONS.values()) and not force:
        return APP_ICONS
    from personalized_agent.agent.character.manager import CharacterManager
    from personalized_agent.ui.tray import icon_image
    characters = CharacterManager(BUILTIN_CHARACTERS, BUILTIN_CHARACTERS)
    face = icon_image(characters, "watermelon", 512)
    face.save(APP_ICONS["png"])
    face.save(APP_ICONS["ico"], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                      (128, 128), (256, 256)])
    face.save(APP_ICONS["icns"])
    return APP_ICONS


def main() -> int:
    force = "--force" in sys.argv[1:]
    path = ensure_assets(force=force)
    print(f"built-in character package ready: {path}")
    for kind, icon in ensure_app_icons(force=force).items():
        print(f"app icon ({kind}) ready: {icon}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

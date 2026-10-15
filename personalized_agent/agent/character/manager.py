"""Character system (sections 6, 112).

A character package is a directory of image assets plus a manifest:

    character_name/
        manifest.json
        idle/  talking/  thinking/  notification/  assets/

Packages come from two places:

* **built-in** packages ship inside the application bundle and are read-only at
  runtime (section 112) - the application never writes there;
* **installed** packages live in the internal data directory and can be added
  later without code changes.

No 3D model generation is involved; appearances are image assets.
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
import base64
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

STATES = ("idle", "talking", "thinking", "notification")
#: The avatar there is when none was chosen.
DEFAULT_CHARACTER = "watermelon"


def avatar_name(config: Any, characters: Any = None) -> str:
    """What the avatar - and so the agent - is called: the name the user
    gave it, or, where they gave none, the avatar's own name."""
    given = str(config.get("agent_name", "") or "").strip()
    if given:
        return given
    chosen = str(config.get("character", DEFAULT_CHARACTER) or DEFAULT_CHARACTER)
    package = None
    if characters is not None:
        try:
            package = characters.get(chosen)
        except Exception:                      # a name, whatever happens
            package = None
    if package and str(package.get("display_name") or "").strip():
        return str(package["display_name"]).strip()
    if chosen == DEFAULT_CHARACTER:
        from .watermelon import DISPLAY_NAME
        return DISPLAY_NAME
    return chosen.replace("-", " ").replace("_", " ").title()

try:
    from PIL import Image, ImageDraw

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    PIL_AVAILABLE = False

PALETTE = {
    "idle": ((92, 140, 230), (60, 100, 190)),
    "talking": ((94, 190, 150), (60, 150, 115)),
    "thinking": ((160, 140, 230), (120, 100, 190)),
    "notification": ((235, 165, 90), (200, 125, 60)),
}


class CharacterManager:
    def __init__(self, builtin_dir: Path, installed_dir: Path) -> None:
        self.builtin_dir = Path(builtin_dir)
        self.installed_dir = Path(installed_dir)
        self.installed_dir.mkdir(parents=True, exist_ok=True)

    # -- lookup -------------------------------------------------------------
    def _roots(self) -> List[tuple]:
        return [(self.installed_dir, "installed"), (self.builtin_dir, "builtin")]

    def list_characters(self) -> List[Dict[str, Any]]:
        packages: Dict[str, Dict[str, Any]] = {}
        for root, origin in reversed(self._roots()):  # installed overrides builtin
            if not root.exists():
                continue
            for directory in sorted(root.iterdir()):
                manifest_path = directory / "manifest.json"
                if not directory.is_dir() or not manifest_path.exists():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except ValueError:
                    log.warning("character package %s has an invalid manifest", directory.name)
                    continue
                packages[directory.name] = {
                    **manifest,
                    "name": manifest.get("name", directory.name),
                    "path": str(directory),
                    "origin": origin,
                    "read_only": origin == "builtin",
                }
        return list(packages.values())

    def _locate(self, name: str) -> Optional[Path]:
        for root, _origin in self._roots():
            candidate = root / name
            if (candidate / "manifest.json").exists():
                return candidate
        return None

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        directory = self._locate(name)
        if directory is None:
            return None
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        frames = {}
        for state in STATES:
            state_dir = directory / state
            frames[state] = (
                [str(p) for p in sorted(state_dir.glob("*.png"))] if state_dir.exists() else []
            )
        return {
            **manifest,
            "name": manifest.get("name", directory.name),
            "path": str(directory),
            "origin": "builtin" if directory.parent == self.builtin_dir else "installed",
            "read_only": directory.parent == self.builtin_dir,
            "frames": frames,
        }

    # -- installation -------------------------------------------------------
    def install_package(self, archive_path: Path) -> Dict[str, Any]:
        """Install a character package from a zip archive, member by member.

        Packages are installed into the internal data directory; the packaged
        application resources stay read-only (section 112).
        """
        archive = Path(archive_path)
        if not zipfile.is_zipfile(archive):
            raise ValueError("character packages must be zip archives")
        root = self.installed_dir.resolve()
        with zipfile.ZipFile(archive) as zf:
            names = [n for n in zf.namelist() if n.strip()]
            top = {Path(n).parts[0] for n in names}
            if len(top) != 1:
                raise ValueError("the archive must contain exactly one character directory")
            package_name = top.pop()
            for name in names:
                destination = (self.installed_dir / name).resolve()
                if root not in destination.parents and destination != root:
                    raise ValueError(f"archive member escapes the character directory: {name}")
            target = (self.installed_dir / package_name).resolve()
            if target.exists():
                shutil.rmtree(target)
            zf.extractall(self.installed_dir)
        if not (self.installed_dir / package_name / "manifest.json").exists():
            shutil.rmtree(self.installed_dir / package_name, ignore_errors=True)
            raise ValueError("the package has no manifest.json")
        return self.get(package_name) or {}

    def remove_package(self, name: str) -> bool:
        target = self.installed_dir / name
        if not (target / "manifest.json").exists():
            return False
        shutil.rmtree(target, ignore_errors=True)
        return True

    def create_avatar(self, image_data: str, name: str = "custom-avatar",
                      pixel_art: bool = False) -> Dict[str, Any]:
        """Create (or replace) an installed avatar from a browser image data URL.

        A custom avatar is a regular character package.  Reusing its one image
        for each state keeps it usable by the desktop animation code without
        pretending that a still image has state-specific animation.
        """
        if not PIL_AVAILABLE:
            raise ValueError("creating an avatar requires Pillow")
        if not isinstance(image_data, str) or not image_data.startswith("data:image/"):
            raise ValueError("avatar must be an image data URL")
        try:
            encoded = image_data.split(",", 1)[1]
            raw = base64.b64decode(encoded, validate=True)
        except (IndexError, ValueError, UnicodeError) as exc:
            raise ValueError("avatar image data is invalid") from exc
        if not raw or len(raw) > 4 * 1024 * 1024:
            raise ValueError("avatar image must be no larger than 4 MB")
        try:
            with Image.open(io.BytesIO(raw)) as source:
                source.verify()
            with Image.open(io.BytesIO(raw)) as source:
                image = source.convert("RGBA")
        except Exception as exc:
            raise ValueError("avatar image could not be read") from exc

        # Crop to the centre square, then use a predictable asset size for the
        # always-on-top desktop window.
        edge = min(image.width, image.height)
        if edge < 1:
            raise ValueError("avatar image has no visible size")
        left, top = (image.width - edge) // 2, (image.height - edge) // 2
        image = image.crop((left, top, left + edge, top + edge)).resize(
            (128, 128), Image.NEAREST if pixel_art else Image.LANCZOS
        )

        target = self.installed_dir / name
        staging = self.installed_dir / (name + ".new")
        shutil.rmtree(staging, ignore_errors=True)
        try:
            for state in STATES:
                state_dir = staging / state
                state_dir.mkdir(parents=True, exist_ok=True)
                image.save(state_dir / "frame_00.png", format="PNG")
            manifest = {
                "name": name,
                "display_name": "Custom avatar",
                "version": "1.0.0",
                "author": "You",
                "states": list(STATES),
                "size": [128, 128],
                "fallback": "image",
            }
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            staging.replace(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self.get(name) or {}

    def described(self, name: str, layers: bool = False) -> Optional[Dict[str, Any]]:
        """One character as the settings page draws it: its name, a still,
        and - for one with a face drawn live - its rig and, asked for, the
        body and leaves the face is drawn on."""
        package = self.get(name)
        if package is None:
            return None
        out = {"name": package["name"],
               "display_name": package.get("display_name") or package["name"],
               "preview": self.preview(package["name"])}
        if package.get("rig"):
            out["rig"] = package["rig"]
            if layers:
                from .watermelon import layers_of
                out["layers"] = layers_of(package["path"])
        return out

    def preview(self, name: str) -> str:
        """Return the first idle frame as a small browser-safe data URL."""
        package = self.get(name)
        frames = (package or {}).get("frames", {}).get("idle", [])
        if not frames:
            return ""
        try:
            return "data:image/png;base64," + base64.b64encode(
                Path(frames[0]).read_bytes()).decode("ascii")
        except OSError:
            return ""

    def describe(self) -> Dict[str, Any]:
        return {
            "builtin_dir": str(self.builtin_dir),
            "installed_dir": str(self.installed_dir),
            "packages": [
                {"name": p["name"], "origin": p["origin"], "read_only": p["read_only"]}
                for p in self.list_characters()
            ],
        }


# ---------------------------------------------------------------------------
# Build-time asset generation
# ---------------------------------------------------------------------------
def generate_default_package(target_dir: Path) -> Path:
    """Create the built-in avatar package: Watermelon (watermelon.py).

    Run at build time (see ``build/prepare_assets.py``) so release artifacts
    ship real image assets and the runtime never writes into its own resources.
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required to generate character assets")
    from .watermelon import write_package
    return write_package(Path(target_dir))


def _generate_round_package(target_dir: Path) -> Path:
    """The round placeholder that came before Watermelon - kept for the
    record, no longer built."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required to generate character assets")
    target_dir = Path(target_dir)
    for state in STATES:
        (target_dir / state).mkdir(parents=True, exist_ok=True)
    (target_dir / "assets").mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "default",
        "display_name": "Aria (default)",
        "version": "1.0.0",
        "author": "Personalized Agent",
        "states": list(STATES),
        "size": [128, 128],
        "fallback": "vector",
    }
    (target_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    size = 128
    for state, (body, outline) in PALETTE.items():
        for frame in range(2):
            image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            bob = 3 if frame else 0
            draw.ellipse((14, 20 + bob, 114, 116 + bob), fill=body + (255,),
                         outline=outline + (255,), width=3)
            eye_y = 52 + bob
            if state == "thinking" and frame:
                draw.line((44, eye_y, 56, eye_y), fill=(255, 255, 255, 255), width=5)
                draw.line((72, eye_y, 84, eye_y), fill=(255, 255, 255, 255), width=5)
            else:
                draw.ellipse((44, eye_y - 6, 56, eye_y + 6), fill=(255, 255, 255, 255))
                draw.ellipse((72, eye_y - 6, 84, eye_y + 6), fill=(255, 255, 255, 255))
                draw.ellipse((48, eye_y - 2, 53, eye_y + 3), fill=(30, 40, 60, 255))
                draw.ellipse((76, eye_y - 2, 81, eye_y + 3), fill=(30, 40, 60, 255))
            mouth_top = 74 + bob
            if state == "talking":
                height = 14 if frame else 7
                draw.ellipse((52, mouth_top, 78, mouth_top + height), fill=(40, 50, 70, 255))
            else:
                draw.arc((50, mouth_top - 6, 80, mouth_top + 12), start=10, end=170,
                         fill=(40, 50, 70, 255), width=4)
            if state == "notification":
                draw.ellipse((92, 12, 118, 38), fill=(230, 70, 70, 255),
                             outline=(255, 255, 255, 255), width=2)
                draw.line((105, 19, 105, 28), fill=(255, 255, 255, 255), width=3)
                draw.ellipse((103, 30, 107, 34), fill=(255, 255, 255, 255))
            image.save(target_dir / state / f"frame_{frame:02d}.png")
    return target_dir

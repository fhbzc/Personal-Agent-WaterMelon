"""THE THINGS THE WORLD CAN HOLD, BY THEME (the user, 2026-11-17: "告诉模型现在已有哪些主题。模型可以根据主题找每个image(每个
有description), 然后根据description可以打开多个图片比较").

The catalogue (api/web/play/catalogue.json, written by scripts/play_catalogue.py from the page's own tables): every picture
a place can hold - its id, a few words of what it is, its size in tiles, its themes. The model growing the world is
given the themes and the things under each; a thing's id is a kind of item it may place. Unsure between some, it names
them and is shown them side by side (`compare_picture`): each picture cut from its sheet, made large, its id under it.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLAY = Path(__file__).resolve().parents[1] / "api" / "web" / "play"
CATALOGUE = PLAY / "catalogue.json"
#: How many may be compared at once; how large each is shown.
COMPARE_MOST, SHOWN_PX = 4, 128

_loaded: Optional[Dict[str, Any]] = None


def catalogue() -> Dict[str, Any]:
    """{"themes": {name: what}, "things": {id: {"what", "themes", "spec"}}} - empty when it cannot be read."""
    global _loaded
    if _loaded is None:
        try:
            _loaded = json.loads(CATALOGUE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _loaded = {"themes": {}, "things": {}}
    return _loaded


def size_of(thing: str) -> Optional[Tuple[int, int]]:
    one = catalogue()["things"].get(thing)
    if not one:
        return None
    spec = one["spec"]
    return (int(spec[3]) if len(spec) > 3 else 1, int(spec[4]) if len(spec) > 4 else 1)


#: THE THEMES IN GROUPS (the user: "如果太多还可以topic上再有hierarchy"): what the model chooses among first.
GROUPS = {
    "nature": ("meadow", "autumn", "forest", "highland", "snow", "desert", "seaside", "lakeside", "cavern", "underwater",
               "botanical", "farmland", "primeval", "great_tree", "pond", "stones", "crystals"),
    "seasons and festivals": ("christmas", "christmas_tree", "newyear", "lantern_gate", "halloween", "valentine",
                              "giant_heart"),
    "town, city and future": ("city", "skyline", "toytown", "scifi", "space", "rocket", "skycity", "station", "market", "fountain",
                              "statue", "cafe", "library", "screening", "studio", "study"),
    "fantasy and the spooky": ("enchanted", "ruins", "ancient_ruins", "graveyard", "mausoleum", "camp"),
    "fun and animals": ("zoo", "funfair", "fair", "giant_pet", "greenhouse"),
    "a building's yard, by its style": ("home", "haunted", "futuristic", "magic", "toy", "ruins_yard", "sky", "skyscraper",
                                        "office", "apartment", "school", "laboratory"),
}


def theme_tree() -> Dict[str, Dict[str, str]]:
    """Every theme in its group, with what it is - no things yet. Themes in no group go under "other"."""
    themes = catalogue()["themes"]
    out: Dict[str, Dict[str, str]] = {}
    placed = set()
    for group, names in GROUPS.items():
        mine = {name: themes[name] for name in names if name in themes}
        if mine:
            out[group] = mine
            placed.update(mine)
    rest = {name: what for name, what in themes.items() if name not in placed}
    if rest:
        out["other"] = rest
    return out


def by_theme(chosen: Optional[List[str]] = None) -> Dict[str, Any]:
    """For the model: each theme (those `chosen`, else all), what it is, and its things as "id: what (w x h)"."""
    data = catalogue()
    out: Dict[str, Any] = {}
    for theme, what in data["themes"].items():
        if chosen is not None and theme not in chosen:
            continue
        things = [f"{name}: {one['what']}" + ("" if size_of(name) == (1, 1) else " ({}x{})".format(*size_of(name)))
                  for name, one in data["things"].items() if theme in one["themes"]]
        if things:
            out[theme] = {"what": what, "things": things}
    return out


def _picture_of(thing: str, side: int):
    from PIL import Image
    spec = catalogue()["things"][thing]["spec"]
    sheet = Image.open(PLAY / f"{spec[0]}.png").convert("RGBA")
    col, row = int(spec[1]), int(spec[2])
    w, h = (int(spec[3]), int(spec[4])) if len(spec) > 4 else (1, 1)
    scaled = len(spec) > 5 and spec[5]
    if scaled:                                      # a giant one: its one small tile
        w = h = 1
    piece = Image.new("RGBA", (w * 16, h * 16), (0, 0, 0, 0))
    for ty in range(h):                             # tile by tile, without the sheet's gaps between
        for tx in range(w):
            piece.paste(sheet.crop(((col + tx) * 17, (row + ty) * 17, (col + tx) * 17 + 16, (row + ty) * 17 + 16)),
                        (tx * 16, ty * 16))
    k = max(1, side // max(piece.width, piece.height))
    return piece.resize((piece.width * k, piece.height * k), Image.NEAREST)


def compare_picture(things: List[str]) -> Optional[Dict[str, str]]:
    """THE THINGS SIDE BY SIDE: each made large on a light ground, its id written under it - a picture for the model
    ({"media_type", "data"}); None when none of them is known or the picture cannot be made."""
    from PIL import Image, ImageDraw
    known = [one for one in things if one in catalogue()["things"]][:COMPARE_MOST]
    if not known:
        return None
    try:
        pictures = [_picture_of(one, SHOWN_PX) for one in known]
    except Exception:
        return None
    cell = SHOWN_PX + 24
    out = Image.new("RGBA", (cell * len(known) + 8, cell + 24), (236, 236, 228, 255))
    draw = ImageDraw.Draw(out)
    for n, (name, picture) in enumerate(zip(known, pictures)):
        x = 8 + n * cell + (SHOWN_PX - picture.width) // 2
        out.alpha_composite(picture, (x, 8 + (SHOWN_PX - picture.height) // 2))
        draw.text((8 + n * cell, SHOWN_PX + 16), name[:22], fill=(30, 30, 30, 255))
    buffer = io.BytesIO()
    out.convert("RGB").save(buffer, "PNG")
    return {"media_type": "image/png", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}

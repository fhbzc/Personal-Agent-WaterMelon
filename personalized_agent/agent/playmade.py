"""WHAT A ROOM IS MADE OF, AND WHAT THE AGENT MAKES FOR IT (the user, 2026-11-19: "墙壁，地板的style再给不同的素材（比如冰面，
大理石，木质，石头等等）"; "区域的整体材质主题和风格的选择会影响内饰，允许模型自己create新的item, 材质，等，并且保存到素材里（写
description), 允许复用").

THE MATERIALS: api/web/play/materials.json - floors (each a 16 x 16 tile, drawn as SVG and put into pixels by the page)
and walls (each one of the four wall sets washed with a colour). Each room's design chooses its floor and its walls, in
its land's own look (the land's "look", chosen with its scene).

WHAT THE AGENT MAKES: a room's design may ask for things and floors the library has not ("make": a name, what it is,
its size) - drawn then in a call of their own (play_world_make, SVG), kept to what is safe (playpieces.clean_svg), and
KEPT IN THE LIBRARY beside the world (made.json, like trees.json: a world drawn again keeps them), each with its
description, offered to every design after: made once, used again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TASK_MAKE = "play_world_make"
MADE_FILE = "made.json"
#: A design may ask for this many new things and floors at most; the library holds this many of each at most.
MAKE_MOST, LIBRARY_MOST = 4, 400
#: A thing made: at most this large indoors (as any thing of a room - the user: "所有内饰只要不超过2*5").
MADE_SIZE_MOST = (2, 5)

_MATERIALS: Optional[Dict[str, Any]] = None


def materials() -> Dict[str, Any]:
    """The materials there are, built in: {"floors": {name: {"description", "svg"|"builtin"}}, "walls": {...}}."""
    global _MATERIALS
    if _MATERIALS is None:
        try:
            _MATERIALS = json.loads((Path(__file__).resolve().parent.parent / "api" / "web" / "play" / "materials.json")
                                    .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _MATERIALS = {"floors": {}, "walls": {}}
    return _MATERIALS


def name_of(text: Any) -> str:
    """A made thing's name, as a kind's: lower case, words joined by "_", 40 letters at most."""
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:40]


def floors_told(library: Dict[str, Any]) -> str:
    built = materials().get("floors") or {}
    made = (library.get("floors") or {})
    return "; ".join(f"{k} ({v.get('description', '')})" for k, v in list(built.items()) + list(made.items()))


def walls_told(library: Optional[Dict[str, Any]] = None) -> str:
    built = materials().get("walls") or {}
    made = ((library or {}).get("walls") or {})
    return "; ".join(f"{k} ({v.get('description', '')})" for k, v in list(built.items()) + list(made.items()))


#: The wall sets a made wall's face is laid over (its outline and shading).
WALL_SETS = ("sandstone", "stone", "marble", "timber")


def things_told(library: Dict[str, Any]) -> str:
    return "; ".join(f"{k} {v['size'][0]}x{v['size'][1]} ({v.get('description', '')})"
                     for k, v in (library.get("items") or {}).items())


ROOM_MADE_NOTE = """ITS SURFACES, ITS OWN (the user, 2026-11-19: "为什么现在模型怎么喜欢用木头墙" - every room of a land \
of wood had copied it): the land's look ("land_look") is where it starts, NOT A RULE - choose from what THIS room is \
and is for: a lab in metal, glass or tiles, a study in dark wood, a vault in stone, a greenhouse in glass; never the \
same as the rooms beside it ("materials_nearby"). "floor_material": one of {floors}; "wall_material" (a room under a \
roof): one of {walls}.
MADE BY YOU, KEPT FOR AGAIN: things made for rooms before, each with what it is: {things}. When nothing there is \
what this room needs, ask for it to be made ("make": up to {most}, each "kind": "thing", "floor" or "wall", "name": a \
few words, "description": what it is and looks like, for a thing "w" x "h" - {size} at most) - and use it already, by \
its name, in this design: it is drawn after and kept for every room to come."""

MAKE_SYSTEM = """You are the maker of things for a small world that pictures the user's life - a 2D pixel world seen \
from the front and a little above, as a classic adventure game. Draw each thing and floor asked for ("make") AS AN \
SVG, richly - it is shown as fine pixel art (each tile 64 x 64 pixels): layers, light from the top left, dark \
outlines, textures and small details, in its land's own look ("land_look") and the room's ("room").
A THING: viewBox "0 0 <w * 16> <h * 16>" - 16 units a tile - standing on the bottom of its box. A FLOOR: viewBox \
"0 0 16 16", one tile that repeats seamlessly. A WALL: the same - its face, one tile that repeats seamlessly (its \
blocks, planks, panes or courses), and "set": the wall it is laid over, one of sandstone, stone, marble, timber. Only shapes, paths, groups and gradients: no text, no images, no \
scripts, no filters.
Answer with only JSON: {"made": [{"name": "<the name asked>", "svg": "<svg ...>...</svg>", "description": "<what \
it is, one sentence>", "set": "<a wall's>"}, ...]}"""


def room_made_note(library: Dict[str, Any]) -> str:
    return ROOM_MADE_NOTE.format(floors=floors_told(library), walls=walls_told(library), things=things_told(library) or "none yet",
                                 most=MAKE_MOST, size=f"{MADE_SIZE_MOST[0]} x {MADE_SIZE_MOST[1]}")


def read_asked(said: Any, library: Dict[str, Any], known: Any) -> List[Dict[str, Any]]:
    """What a design asked to be made, kept to what can be: a thing or a floor, a name not yet a kind or a material
    (`known`: whether a name is), a thing as large as MADE_SIZE_MOST - MAKE_MOST at most."""
    out: List[Dict[str, Any]] = []
    if not isinstance(said, dict):
        return out
    for one in said.get("make") or []:
        if not isinstance(one, dict) or len(out) >= MAKE_MOST:
            continue
        kind, name = str(one.get("kind") or ""), name_of(one.get("name"))
        if kind not in ("thing", "floor", "wall") or not name or known(name) or any(o["name"] == name for o in out):
            continue
        description = " ".join(str(one.get("description") or "").split())[:300]
        if kind in ("floor", "wall"):
            out.append({"kind": kind, "name": name, "description": description})
            continue
        try:
            w, h = int(one.get("w") or 1), int(one.get("h") or 1)
        except (TypeError, ValueError):
            continue
        if not (1 <= min(w, h) and ((w <= MADE_SIZE_MOST[0] and h <= MADE_SIZE_MOST[1]) or
                                    (w <= MADE_SIZE_MOST[1] and h <= MADE_SIZE_MOST[0]))):
            continue
        out.append({"kind": "thing", "name": name, "description": description, "w": w, "h": h})
    return out


def read_made(said: Any, asked: List[Dict[str, Any]], at: str) -> Dict[str, Dict[str, Any]]:
    """The things and floors as drawn, kept to what is safe (playpieces.clean_svg): {name: entry} for the library -
    a thing {"description", "size", "svg", "made"}, a floor {"description", "svg", "made"}."""
    from .playpieces import clean_svg
    by = {a["name"]: a for a in asked}
    out: Dict[str, Dict[str, Any]] = {}
    for one in (said.get("made") if isinstance(said, dict) else None) or []:
        if not isinstance(one, dict):
            continue
        want = by.get(name_of(one.get("name")))
        if want is None:
            continue
        size = (want["w"] * 16, want["h"] * 16) if want["kind"] == "thing" else (16, 16)
        svg = clean_svg(one.get("svg"), *size)
        if not svg:
            continue
        out[want["name"]] = {"kind": want["kind"], "description": " ".join(str(one.get("description") or
                                                                              want["description"]).split())[:300],
                             "svg": svg, "made": at, **({"size": [want["w"], want["h"]]} if want["kind"] == "thing" else {}),
                             **({"set": one.get("set") if one.get("set") in WALL_SETS else "stone"}
                                if want["kind"] == "wall" else {})}
    return out


def load_library(folder: Path) -> Dict[str, Any]:
    try:
        said = json.loads((folder / MADE_FILE).read_text(encoding="utf-8"))
        return {"items": dict(said.get("items") or {}), "floors": dict(said.get("floors") or {}),
                "walls": dict(said.get("walls") or {})}
    except (OSError, ValueError):
        return {"items": {}, "floors": {}, "walls": {}}


def keep_library(folder: Path, library: Dict[str, Any]) -> None:
    for key in ("items", "floors", "walls"):
        ones = library.get(key) or {}
        while len(ones) > LIBRARY_MOST:
            ones.pop(next(iter(ones)))
    path = folder / MADE_FILE
    temp = path.with_suffix(".json.new")
    temp.write_text(json.dumps(library, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


__all__ = ["TASK_MAKE", "MADE_FILE", "MAKE_MOST", "materials", "name_of", "room_made_note", "MAKE_SYSTEM",
           "read_asked", "read_made", "load_library", "keep_library", "floors_told", "walls_told", "things_told"]

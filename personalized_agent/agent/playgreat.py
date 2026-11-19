"""A PROJECT'S GREAT GROUND (the user, 2026-11-19): when a project's places, gathered, are large enough, they stop being
blocks side by side and become ONE design - "这几个区域你应该是能识别出来是同一个project下的，但是能不能这样整体变成一个更融合
的design, 而不是一个个block ... 另外你的区域可以是圆形的，平行四边形的，不一定是长方形"; "当出现融合的时候，融合size大到一定程度，
按照大型物品设计来规划，记录面积和位置之后要选择形状，重新设计内容"; "大型项目设计可以是纯露天，可以是露天室内结合，也可以是纯室内，
让agent根据旁边景观和场景设计"; "融合之后不要独立房子".

In order: its footprint kept (area, middle, box); a SURVEY of where it stands - the land, its lie, its neighbours,
nothing of the project - and a search of the web for human places of the kind that would belong there, to draw from
(play_world_great_survey); its SHAPE; its PLAN - where each task's part lies in the shape, open or under a roof, the
ways through, its stages along a way, its featured steps (play_world_great_plan); then its open ground designed as one
(the room design, the whole ground at once, a few cards a call) and looked at once.

What the content shows (the user: "内容设计主要要体现 1. task 树 2. progress (第一阶段成果，第二阶段成果) 3. featured step
(特别难的，花了特别多时间的步骤）"): the tree as the ground's layout, the stages as a way walked with each one's result,
the steps that stood out - by their time (the code's) and by what the agent judged hardest - made the great things.

The rules are the code's: the shape's tiles (shape_has, the same in play-regions.js playShapeHas), a part inside the
shape and clear of the others, every task given a part, the mode kept to.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from . import playshapes as shapes

#: A project's places, gathered, at least this large (tiles) or this many, and it is one great ground.
GREAT_AREA_LEAST, GREAT_PLACES_LEAST = 900, 3
#: Designed again when grown this much since, or when its tasks changed (one joined, one split off).
GREAT_REDO_GROWN = 1.4
#: The plan's coarse grid: a cell this many tiles each way.
COARSE = 4
#: A great ground's open design: its answer at most this long.
GREAT_ANSWER_MOST = 16000

TASK_GREAT_SURVEY = "play_world_great_survey"
TASK_GREAT_PLAN = "play_world_great_plan"

SHAPES = {
    "ellipse": "round or oval - a park, a campus green, a crater",
    "ring": "a ring round an open middle (a lake, a plaza) - a cloister, a ring town",
    "hexagon": "six-sided - a fort, a honeycomb, a planned town",
    "parallelogram": "slanted - a terrace on a slope, a quay along water",
    "blob": "a free, natural outline - a valley floor, a clearing, an island",
    "l_shape": "an L - two wings meeting",
    "band": "a long diagonal band - along a river, a ridge, a railway",
    "rect": "a plain rectangle - a walled compound, a city block",
    **shapes.OUTLINES,                                   # a star, a triangle, a heart... (agent/playshapes.py)
}
MODES = {"open": "all open air - fields, gardens, a park, a fair",
         "mixed": "open ground with halls on it - an estate, a campus, a village",
         "indoor": "all under a roof - one great building of halls and courts"}
PART_SHAPES = ("ellipse", "blob", "rect", "hexagon", *shapes.OUTLINES)


def shape_has(shape: Optional[Dict[str, Any]], box: Tuple[int, int, int, int], x: int, y: int) -> bool:
    """WHETHER A TILE IS IN THE SHAPE (play-regions.js playShapeHas: the same sums): `box` its left, top, width,
    height; x, y the tile. No shape: the whole box."""
    left, top, w, h = box
    if not (left <= x < left + w and top <= y < top + h):
        return False
    kind = str((shape or {}).get("kind") or "rect")
    if kind == "rect" or w < 3 or h < 3:
        return True
    seed = float((shape or {}).get("seed") or 0) % 6.283
    u = (x + 0.5 - left) / w * 2 - 1
    v = (y + 0.5 - top) / h * 2 - 1
    d = u * u + v * v
    polygon = shapes.polygon_has(kind, u, v)
    if polygon is not None:                             # a star, a triangle, a heart... (agent/playshapes.py)
        return polygon
    if kind == "ellipse":
        return d <= 1.0
    if kind == "ring":
        return 0.16 <= d <= 1.0
    if kind == "hexagon":
        return abs(u) <= 1.0 - 0.5 * abs(v)
    if kind == "parallelogram":
        return abs(u + 0.35 * v) <= 0.65
    if kind == "band":
        return abs(u - 0.6 * v) <= 0.55
    if kind == "l_shape":
        corner = int(seed * 4 / 6.283) % 4
        su, sv = (1, -1, -1, 1)[corner], (-1, -1, 1, 1)[corner]
        return not (u * su > 0.15 and v * sv > 0.15)
    if kind == "blob":
        angle = math.atan2(v, u)
        reach = 0.88 + 0.08 * math.sin(3 * angle + seed) + 0.06 * math.sin(5 * angle + 2 * seed) + \
            0.04 * math.sin(7 * angle + 3 * seed)
        return math.sqrt(d) <= reach
    return True


def shape_tiles(shape: Optional[Dict[str, Any]], box: Tuple[int, int, int, int]) -> set:
    left, top, w, h = box
    return {(x, y) for x in range(left, left + w) for y in range(top, top + h) if shape_has(shape, box, x, y)}


def coarse_grid(shape: Optional[Dict[str, Any]], box: Tuple[int, int, int, int]) -> List[str]:
    """The shape as the plan sees it: a COARSE x COARSE cell "#" when most of it is in the shape, else "."."""
    left, top, w, h = box
    rows = []
    for cy in range(math.ceil(h / COARSE)):
        row = ""
        for cx in range(math.ceil(w / COARSE)):
            inside = sum(1 for dx in range(COARSE) for dy in range(COARSE)
                         if shape_has(shape, box, left + cx * COARSE + dx, top + cy * COARSE + dy))
            row += "#" if inside >= COARSE * COARSE / 2 else "."
        rows.append(row)
    return rows


# -- 1. the survey: where it stands, and human places to draw from ---------------------------------------------------
SURVEY_SYSTEM = """You are the landscape architect of one great ground in a small world that pictures the user's \
life. Before anything is designed, LOOK AT WHERE IT STANDS ("here": its land, the lie of it, its edges, its weather; \
"around": the lands and places beside it) - only the place, nothing of the work that will fill it. Describe the \
surroundings as a site survey would: the terrain, the views, the water, what stands near, what it asks of anything \
built there. Then SEARCH THE WEB for real human places of the kind that would belong on such a site - a hillside \
monastery, canal warehouses, a Japanese stroll garden, a desert caravanserai, a fairground on a pier - and note what \
each does: its layout, its materials, how people move through it, its one unforgettable element. From all that \
choose how the ground is built ("mode": {modes}) and its outline ("shape": {shapes}).
Answer with only JSON: {{"surroundings": "<at most 4 sentences>", "references": [{{"name": "<the real place or kind \
of place>", "where": "<where it is, or its tradition>", "url": "<a page about it>", "traits": "<its layout, \
materials, movement, its one great element, at most 2 sentences>"}}, ...2 to 4], "mode": "<one of the modes>", \
"shape": "<one of the shapes>", "why": "<at most 2 sentences: why this mode and outline here>"}}"""


def survey_system() -> str:
    return SURVEY_SYSTEM.format(modes="; ".join(f"{k} ({v})" for k, v in MODES.items()),
                                shapes="; ".join(f"{k} ({v})" for k, v in SHAPES.items()))


def read_survey(found: Any) -> Dict[str, Any]:
    """The survey as said, checked: a mode and a shape of the lists (else mixed, a blob), references with a name."""
    found = found if isinstance(found, dict) else {}
    short = lambda text, n: " ".join(str(text or "").split())[:n]       # noqa: E731
    refs = []
    for one in found.get("references") or []:
        if isinstance(one, dict) and short(one.get("name"), 80):
            url = str(one.get("url") or "").strip()
            refs.append({"name": short(one.get("name"), 80), "where": short(one.get("where"), 80),
                         "traits": short(one.get("traits"), 400),
                         **({"url": url[:400]} if url.startswith(("http://", "https://")) else {})})
    mode = str(found.get("mode") or "")
    shape = str(found.get("shape") or "")
    return {"surroundings": short(found.get("surroundings"), 800), "references": refs[:4],
            "mode": mode if mode in MODES else "mixed", "shape": shape if shape in SHAPES else "blob",
            "why": short(found.get("why"), 300)}


# -- 2. the plan: where each part lies, the ways, the stages, the steps that stood out --------------------------------
PLAN_SYSTEM = """You are the master planner of one great ground in a small world that pictures the user's life: ONE \
project's work made one place - not blocks side by side, one design. You have the site survey and the human places to \
draw from ("survey"), its outline as a coarse grid ("grid": each cell {coarse} x {coarse} tiles, "#" inside the \
outline, "." outside), how it is built ("mode"), the project's idea and muses ("concept", "muses"), its tasks (each \
with its time) and each task's work as a tree of cards ("work": each card's id, title, status, depth, how long it \
took in minutes and in how many tries, how many of them a putting right).
WHAT THE GROUND SHOWS:
1. THE TREE - the ground's own layout: the project's root work its heart; each great branch a part of it; the cards \
under a branch within that part.
2. THE PROGRESS - its STAGES in the order they were done, as a way walked through the ground: each stage what it made \
(its result) standing where it is reached; the stage under way being built; those to come waiting. Group the cards \
into 2 to {stages} stages by when and what they were ("stages": each a title and its cards' ids).
3. THE STEPS THAT STOOD OUT - "featured": the 1 or 2 you judge the HARDEST - by what they were, not only their time \
("stood_out_by_time" are those the clock picked) - made the great things of the ground, where the eye goes.
EACH TASK ITS PART ("parts"): where it lies in the grid ("cell": [column, row, columns wide, rows high], all on "#"), \
its kind ({open_kinds} in the open; {indoor_kinds} under a roof - a hall of the one building, never a house apart; \
"mode" open: none under a roof; indoor: all), its outline ({part_shapes}), why there. EACH PART AS LARGE AS ITS \
WORK, never crowded: about 36 tiles to begin with and 9 more for each of its task's cards. Parts may touch but not \
overlap. THE HALLS UNDER A ROOF ARE THE ROOMS OF ONE BUILDING, side by side in a row in the order given: each says \
"open_to_next": true (a door through the wall it shares with the next room) or false (the wall closed - a room of \
its own) - some joined, some not, as the rooms of a real building are.
Answer with only JSON: {{"idea": "<the ground's one idea, at most 2 sentences>", "parts": [{{"task": "<the task's \
id>", "kind": "<a kind>", "cell": [c, r, w, h], "shape": "<an outline>", "open_to_next": true or false (a hall), \
"why": "<at most 2 sentences>"}}, ...], \
"stages": [{{"title": "<a few words>", "cards": ["<card id>", ...], "cell": [c, r]}}, ...], "featured": [{{"card": \
"<card id>", "why": "<what made it hard, at most 2 sentences>"}}, ...up to 2], "ways": "<how one walks through it, at \
most 2 sentences>"}}"""


def plan_system(open_kinds: List[str], indoor_kinds: List[str], stages_most: int) -> str:
    return PLAN_SYSTEM.format(coarse=COARSE, stages=stages_most, open_kinds=", ".join(open_kinds),
                              indoor_kinds=", ".join(indoor_kinds), part_shapes=", ".join(PART_SHAPES))


def read_plan(found: Any, grid: List[str], tasks: List[str], cards: Dict[str, List[str]], mode: str,
              open_kinds: List[str], indoor_kinds: List[str]) -> Dict[str, Any]:
    """THE PLAN AS SAID, KEPT TO WHAT CAN BE (the code's): a part for the project's own tasks only, one each, of a kind
    the mode allows, on cells inside the outline, clear of the parts before it; stages of the project's own cards, each
    card in one; featured steps its own cards, two at most. What it left out is the code's to place (_lay_great)."""
    found = found if isinstance(found, dict) else {}
    short = lambda text, n: " ".join(str(text or "").split())[:n]       # noqa: E731
    rows, cols = len(grid), max((len(r) for r in grid), default=0)
    inside = {(c, r) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch == "#"}
    taken: set = set()
    parts: List[Dict[str, Any]] = []
    for one in found.get("parts") or []:
        if not isinstance(one, dict):
            continue
        task, kind = str(one.get("task") or ""), str(one.get("kind") or "")
        if task not in tasks or any(p["task"] == task for p in parts):
            continue
        allowed = (open_kinds if mode == "open" else indoor_kinds if mode == "indoor" else open_kinds + indoor_kinds)
        if kind not in allowed:
            continue
        try:
            c, r, w, h = (int(v) for v in (one.get("cell") or [])[:4])
        except (TypeError, ValueError):
            continue
        if w < 1 or h < 1 or c < 0 or r < 0 or c + w > cols or r + h > rows:
            continue
        cells = {(c + dx, r + dy) for dx in range(w) for dy in range(h)}
        if len(cells & inside) < len(cells) * 0.6 or cells & taken:
            continue
        taken |= cells
        shape = str(one.get("shape") or "")
        parts.append({"task": task, "kind": kind, "cell": [c, r, w, h], "indoor": kind in indoor_kinds,
                      "shape": shape if shape in PART_SHAPES else "rect", "why": short(one.get("why"), 300),
                      "open_to_next": one.get("open_to_next") is not False})
    own = {i for ids in cards.values() for i in ids}
    used: set = set()
    stages = []
    for one in found.get("stages") or []:
        if not isinstance(one, dict):
            continue
        mine = [i for i in (one.get("cards") or []) if isinstance(i, str) and i in own and i not in used]
        if not mine:
            continue
        used |= set(mine)
        try:
            at = [int(v) for v in (one.get("cell") or [])[:2]]
        except (TypeError, ValueError):
            at = []
        stages.append({"title": short(one.get("title"), 60), "cards": mine,
                       **({"cell": at} if len(at) == 2 and tuple(at) in inside else {})})
    featured = []
    for one in found.get("featured") or []:
        if isinstance(one, dict) and str(one.get("card") or "") in own and len(featured) < 2 \
                and all(f["card"] != one["card"] for f in featured):
            featured.append({"card": str(one["card"]), "why": short(one.get("why"), 300), "by": "the agent"})
    return {"idea": short(found.get("idea"), 300), "parts": parts, "stages": stages, "featured": featured,
            "ways": short(found.get("ways"), 300)}


def part_rect(part: Dict[str, Any], box: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """A part's tiles: its cells as tiles of the ground - left, top, width, height."""
    left, top, w, h = box
    c, r, cw, ch = part["cell"]
    x0, y0 = left + c * COARSE, top + r * COARSE
    return x0, y0, min(cw * COARSE, left + w - x0), min(ch * COARSE, top + h - y0)


__all__ = ["GREAT_AREA_LEAST", "GREAT_PLACES_LEAST", "GREAT_REDO_GROWN", "COARSE", "GREAT_ANSWER_MOST",
           "TASK_GREAT_SURVEY", "TASK_GREAT_PLAN", "SHAPES", "MODES", "PART_SHAPES", "shape_has", "shape_tiles",
           "coarse_grid", "survey_system", "read_survey", "plan_system", "read_plan", "part_rect"]

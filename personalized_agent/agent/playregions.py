"""THE WORLD'S REGIONS - EACH PERSON'S WORLD ITS OWN AT A GLANCE (the user, 2026-11-17: "每个人的世界design 如何让它有更不同的
风格呢，最好每个人的经历不同，他们的世界一眼看上去就很不同"; "模型一开始，用户数据不多的时候，只能画新手村，这个时候风格什么比较类似，
只是细节不同。当模型对用户数据比较多之后，可以用不同风格的模板，这也是用户走出新手村的地图设计，然后画出非常不一样的东西 ... 如果用户
进行activity的巨大变化，风格可以再迁移。几个不同风格的中间用比较smooth的连接 ... 尽可能有多的不同种风格，另外布局，style, 内容
主题，都有不同的setting (这样combination会很多)").

A REGION is a block of the world with one style, chosen by the agent from six settings that combine freely:
  biome (the land), layout (how its rooms stand and its roads run), palette (its light and colour), architecture (what
  its walls are made of), theme (what stands about outdoors) and landmark (the one big thing at its heart).
Everyone starts in the same STARTING VILLAGE; once the agent knows enough of the user (LEAVE_*), it opens the first
region beyond it in a style of its own choosing; a great change in what the user does opens another (CHANGE_DAYS
apart at least). Regions never move nor change once made; the ground between two blends from one to the other
(api/web/play.js), and a road joins each to the one it was opened from.

Laid out here: where a region is and which of its cells a room takes next. Drawn in the page.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

#: The settings, each the agent's to choose (the page knows how to draw each - api/web/play-regions.js).
BIOMES = ("meadow", "autumn", "enchanted", "desert", "seaside", "forest", "highland", "snow", "city", "farmland",
          "cavern", "lakeside", "toytown", "underwater", "skycity", "space", "scifi", "zoo", "ruins", "graveyard", "christmas", "newyear", "halloween",
          "valentine", "funfair", "botanical", "primeval")
LAYOUTS = ("village", "grid", "plaza", "river", "islands", "scattered")
PALETTES = ("day", "dawn", "dusk", "night", "spring", "summer", "autumn", "winter", "pastel", "sepia", "misty")
ARCHITECTURES = ("sandstone", "stone", "marble", "timber")
THEMES = ("scholar", "maker", "traveller", "carer", "performer", "athlete", "dreamer", "explorer", "merchant",
          "quiet")
LANDMARKS = ("fountain", "statue", "great_tree", "camp", "station", "stones", "pond", "market", "crystals", "giant_pet",
             "rocket", "ancient_ruins", "mausoleum", "fair", "greenhouse", "christmas_tree", "lantern_gate", "giant_heart", "skyline")
#: Its sky (api/web/play-sky.js): changing by its land's chances, always one weather, or an eternal season or hour.
WEATHERS = ("changing", "fair", "rainy", "snowy", "foggy", "eternal_night", "eternal_winter", "eternal_dusk",
            "eternal_spring")
#: Its street lights (api/web/play-regions.js): a lamp, a gas lamp, a torch, a brazier - else its land's own.
LIGHTS = ("lamp", "gaslamp", "torch", "brazier")
SETTINGS = {"biome": BIOMES, "layout": LAYOUTS, "palette": PALETTES, "architecture": ARCHITECTURES,
            "theme": THEMES, "landmark": LANDMARKS}

#: Everyone's beginning: the same for all, only what is in it differs.
STARTER = {"biome": "meadow", "layout": "village", "palette": "day", "architecture": "sandstone", "theme": "quiet",
           "landmark": "", "weather": "changing"}

#: A region is SPAN x SPAN cells (its middle cell (0, 0)); BAND tiles of blended ground between two.
SPAN, BAND = 5, 14
#: Rooms in a region at most - the rest of its cells its land.
ROOMS_MOST = 12
#: Leaving the starting village: at least this many things made and days drawn first.
LEAVE_ITEMS, LEAVE_DAYS = 12, 28
#: Another region, for a great change: at least this many days drawn after the last one opened.
CHANGE_DAYS = 30


def region_cells(count: int) -> List[Tuple[int, int]]:
    """Where regions stand, by the grid of regions: the start in the middle, then a ring at a time - right, then
    below, first (the same order as the rooms of the starting village)."""
    out: List[Tuple[int, int]] = [(0, 0)]
    ring = 1
    while len(out) < count:
        around = [(x, y) for y in range(-ring, ring + 1) for x in range(-ring, ring + 1)
                  if max(abs(x), abs(y)) == ring]
        around.sort(key=lambda one: (abs(one[1]), abs(one[0]), one[1] < 0, one[0] < 0))
        out.extend(around)
        ring += 1
    return out[:count]


def spread_of(region: Dict[str, Any]) -> Tuple[int, int]:
    """How many region cells a region takes across and down - a great project's district more than one."""
    s = region.get("spread") or [1, 1]
    return max(1, int(s[0])), max(1, int(s[1]))


def cells_of(region: Dict[str, Any]) -> List[Tuple[int, int]]:
    """Every region cell a region stands on: its own, and those it spreads to, right and down."""
    cx, cy = region["cell"]
    sx, sy = spread_of(region)
    return [(cx + i, cy + j) for j in range(sy) for i in range(sx)]


def origin(cell: Tuple[int, int], cell_w: int, cell_h: int) -> Tuple[int, int]:
    """The tile of a region's middle cell's corner - the starting village's is (0, 0), as the world always had."""
    return cell[0] * (SPAN * cell_w + BAND), cell[1] * (SPAN * cell_h + BAND)


def _within() -> List[Tuple[int, int]]:
    half = SPAN // 2
    return [(x, y) for y in range(-half, half + 1) for x in range(-half, half + 1)]


def room_order(region: Dict[str, Any]) -> List[Tuple[int, int]]:
    """THE CELLS A REGION'S ROOMS TAKE, in turn - its layout's shape. The middle cell kept for the landmark, but in the
    starting village (which has none, and whose first room - home - was always there)."""
    layout = region.get("layout") or "village"
    cells = _within()
    ring = lambda one: (max(abs(one[0]), abs(one[1])), abs(one[1]), abs(one[0]), one[1] < 0, one[0] < 0)
    if layout == "grid":                 # a long street first: its row filled out, then the next
        order = sorted(cells, key=lambda one: (abs(one[1]), one[1] < 0, abs(one[0]), one[0] < 0))
    elif layout == "river":              # its banks: the river runs through the row below the middle, no room in it
        rows = {0: 0, 2: 1, -1: 2, -2: 3}
        order = sorted((one for one in cells if one[1] != 1),
                       key=lambda one: (rows[one[1]] // 2, abs(one[0]), one[0] < 0, rows[one[1]]))
    elif layout == "islands":            # every other cell first: each an island of its own, water round it
        order = sorted(cells, key=lambda one: ((one[0] + one[1]) % 2, *ring(one)))
    elif layout == "scattered":          # here and there, its own way for each region
        seed = str(region.get("id") or "")
        order = sorted(cells, key=lambda one: hashlib.sha1(f"{seed}:{one}".encode()).hexdigest())
    else:                                # village, plaza: nearest the middle first
        order = sorted(cells, key=ring)
    if not region.get("starter"):
        order = [one for one in order if one != (0, 0)]
    return order[:ROOMS_MOST]


#: A LANDMARK WHERE IT BELONGS (the user, 2026-11-18: a skyline on a lakeshore "明显很突兀，和场景什么的都不协调"): those
#: that fit only some lands, and what stands instead elsewhere.
LANDMARK_LANDS = {"skyline": ("city", "scifi", "space", "skycity"), "rocket": ("space", "scifi", "city"),
                  "mausoleum": ("graveyard", "halloween", "ruins"), "christmas_tree": ("christmas", "snow"),
                  "lantern_gate": ("newyear",), "giant_heart": ("valentine",), "fair": ("funfair", "toytown", "city")}


def fitting_landmark(landmark: str, biome: str) -> str:
    lands = LANDMARK_LANDS.get(landmark)
    return landmark if not lands or biome in lands else "fountain"


def chosen(said: Any) -> Optional[Dict[str, str]]:
    """A style as the model gave it, checked: each setting one of its own, or the region is not opened."""
    if not isinstance(said, dict):
        return None
    out = {}
    for key, allowed in SETTINGS.items():
        value = str(said.get(key) or "").strip().lower()
        if value not in allowed:
            return None
        out[key] = value
    out["landmark"] = fitting_landmark(out["landmark"], out["biome"])
    weather = str(said.get("weather") or "").strip().lower()
    out["weather"] = weather if weather in WEATHERS else "changing"     # a sky of its own, else one that changes
    lights = str(said.get("lights") or "").strip().lower()
    if lights in LIGHTS:                                                  # its street lights, else its land's
        out["lights"] = lights
    return out


def migrate(world: Dict[str, Any], cell_w: int, cell_h: int) -> bool:
    """A world from before regions: all of it the starting village, nothing moved. True when it changed."""
    if world.get("regions"):
        return False
    world["regions"] = [{"id": "r1", "starter": True, "name": "Starting Village",
                         "why": "Where everyone begins.", "made": world.get("born") or "", "cell": [0, 0],
                         "ox": 0, "oy": 0, "from": "", **STARTER}]
    for zone in world.get("zones") or []:
        zone.setdefault("region", "r1")
    return True


#: Settings a land apart starts from, its own kind's put over them.
SETTINGS_DEFAULT = {"biome": "seaside", "layout": "islands", "palette": "day", "architecture": "stone", "theme": "quiet",
                    "landmark": "none"}


#: AN AREA HOLDS THIS MANY REGIONS (the user, 2026-11-18: "世界如果继续变大 可以有多个区域 一个区域必须传送到下一个区域"): the
#: next begins an area of its own, apart, reached by its gate portal - only one area is laid out at a time.
AREA_REGIONS = 9


def area_of(region: Dict[str, Any]) -> str:
    return str(region.get("area") or "a1")


def area_cells(world: Dict[str, Any], area: str) -> int:
    """How many region cells an area's lands take - a great district's several counted."""
    return sum(len(cells_of(one)) for one in world.get("regions") or [] if area_of(one) == area and not one.get("apart"))


def open_region(world: Dict[str, Any], style: Dict[str, str], name: str, why: str, at: str,
                days: Optional[Tuple[str, str]], cell_w: int, cell_h: int, travel: bool = False) -> Dict[str, Any]:
    """A new region beside the others, joined to the one the user is in now - or, its area full, the first of a new
    area, apart from every other region and joined to none (reached by the areas' gates)."""
    regions = world["regions"]
    taken = {cell for one in regions for cell in cells_of(one)}
    area = area_of(regions[-1])
    joined: Optional[str] = regions[-1]["id"]
    # A NEW AREA when this one is full - its cells, a district's several counted - or the user has travelled (the
    # user: "地图过大换一个完整区域（或者比如用户travel到另一个国家，换一个完整区域）").
    if travel or area_cells(world, area) >= AREA_REGIONS:
        area = f"a{1 + max(int(area_of(one)[1:] or 1) for one in regions)}"
        joined = None
        cell = next(one for one in region_cells(len(taken) * 9 + 60)
                    if one not in taken and not any((one[0] + dx, one[1] + dy) in taken
                                                     for dx in (-1, 0, 1) for dy in (-1, 0, 1)))
    else:
        mine = {cell for one in regions if area_of(one) == area for cell in cells_of(one)}
        cells = [one for one in region_cells(len(taken) * 9 + 60) if one not in taken]
        # Beside its own area's regions, not another area's.
        cell = next((one for one in cells if any((one[0] + dx, one[1] + dy) in mine for dx in (-1, 0, 1) for dy in (-1, 0, 1))
                     and not any((one[0] + dx, one[1] + dy) in taken - mine for dx in (-1, 0, 1) for dy in (-1, 0, 1))),
                    cells[0])
    ox, oy = origin(cell, cell_w, cell_h)
    region = {"id": f"r{len(regions) + 1}", "starter": False, "name": name[:60], "why": why[:500], "made": at,
              "cell": list(cell), "ox": ox, "oy": oy, "from": joined, "area": area, **style,
              **({"travel": True} if travel else {})}
    if days:
        region["days"] = [days[0][:10], days[1][:10]]
    regions.append(region)
    return region


#: What a place apart is: an island in the sea, or a cave alone in the dark - reached only by a portal.
APART = {"island": {"biome": "seaside", "layout": "islands"}, "cave": {"biome": "cavern", "layout": "scattered"}}


def open_apart(world: Dict[str, Any], kind: str, name: str, why: str, at: str, cell_w: int, cell_h: int,
               style: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """A LAND APART (the user: "孤岛，或者孤独山洞，反正只能用传送门到"): a cell with no land on any side of it, joined to
    nothing - no road to it; the road of their life does not go there either. Reached by the portals only."""
    regions = world["regions"]
    taken = {cell for one in regions for cell in cells_of(one)}
    cell = next(one for one in region_cells(len(taken) * 9 + 60)
                if one not in taken and not any((one[0] + dx, one[1] + dy) in taken
                                                 for dx in (-1, 0, 1) for dy in (-1, 0, 1)))
    ox, oy = origin(cell, cell_w, cell_h)
    settings = dict(SETTINGS_DEFAULT)
    settings.update(style or {})
    settings.update(APART.get(kind, APART["island"]))
    region = {"id": f"r{len(regions) + 1}", "starter": False, "name": name[:60], "why": why[:500], "made": at,
              "cell": list(cell), "ox": ox, "oy": oy, "from": None, "apart": kind if kind in APART else "island",
              "area": area_of(regions[-1]), **settings}
    regions.append(region)
    return region


def free_cell(world: Dict[str, Any], region: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """The next cell a room takes in a region, or None when it is full."""
    # Every cell a place stands on - a mansion's four, not its first alone.
    taken = {tuple(cell) for one in world.get("zones") or [] if one.get("region") == region["id"]
             for cell in (one.get("cells") or [one["cell"]])}
    return next((one for one in room_order(region) if one not in taken), None)


def full(world: Dict[str, Any], region: Dict[str, Any]) -> bool:
    """No room left in a region for even a small room."""
    from . import playplaces as places
    w, h = places.FIRST_INDOOR
    return places.find_spot(world, region, "home", w, h, places.heart(world, region), "full") is None


def may_leave(world: Dict[str, Any], drawn_days: int) -> Optional[str]:
    """Whether a new region may be opened now, and why ("leave" the starting village, "change" for a great change,
    "full" the current one has no room) - None when not."""
    regions = world.get("regions") or []
    if not regions:
        return None
    current = regions[-1]
    if full(world, current):
        return "full"
    if current.get("starter"):
        return "leave" if len(world.get("items") or []) >= LEAVE_ITEMS and drawn_days >= LEAVE_DAYS else None
    since = str((current.get("days") or ["", ""])[1] or current.get("made") or "")[:10]
    caught = str(world.get("caught_up") or "")[:10]
    try:
        from datetime import date
        apart = (date.fromisoformat(caught) - date.fromisoformat(since)).days
    except ValueError:
        return None
    return "change" if apart >= CHANGE_DAYS else None


__all__ = ["area_cells", "spread_of", "cells_of", "LIGHTS", "WEATHERS", "BIOMES", "LAYOUTS", "PALETTES", "ARCHITECTURES", "THEMES", "LANDMARKS", "SETTINGS", "STARTER", "SPAN",
           "BAND", "ROOMS_MOST", "region_cells", "origin", "room_order", "chosen", "migrate", "open_region",
           "free_cell", "may_leave"]

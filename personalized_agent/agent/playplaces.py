"""PLACES THAT GROW GRAND (the user, 2026-11-17: "我觉得可以让模型扩大房间，加小物件等。另外你现在也不一定是房间，包括室外景观也是
可以的，另外房间你参考一下豪宅的设计，用更艺术感的房间（大泳池，大公园，之类）"; "风格可以参考豪宅，也可以不要，根据事情的复杂程度，
设计，允许重设计等等"; "重设计不要丢掉之前的细节，只允许重新摆放"; "比如一开始是小房间，后面是大豪宅").

A place stands on a block of its region's cells: one cell at first, then - enlarged, or redesigned at a larger scale -
two side by side, two one above the other, or four. A block never crosses its region's middle road (its row and its
column of roads stay whole, so every place can still be reached) nor takes the region's middle cell (the landmark's).
A building more than a cell is parted inside into rooms by walls with doorways; an outdoor place - a garden, a park, a
pool, a courtyard, a terrace - is open, a pool's water in its middle. Redesigned or enlarged, a place keeps every thing
in it, laid out again in its new size; nothing is ever lost. The page draws the same walls from the same rule
(api/web/play-regions.js playInnerWalls).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

#: How a building is built: its walls' material and floor (the page's), from a cottage to a mansion.
STYLES = ("cottage", "classic", "modern", "rustic", "zen", "industrial", "mansion", "toy", "magic", "futuristic", "ruins",
          "sky", "haunted", "skyscraper", "school", "laboratory", "office", "apartment")
#: How large: cells across x down.
SCALES = {"small": (1, 1), "large": (2, 1), "tall": (1, 2), "estate": (2, 2)}
#: Open places, without walls - each drawn its own way.
OUTDOOR = ("garden", "park", "pool", "courtyard", "terrace", "lake", "waterfall", "plaza", "grove", "meadow", "mountain",
           "volcano", "peak", "sea_tunnel", "roller_coaster", "cemetery", "funfair", "estate")
#: Of those, nature's (the user, 2026-11-17: "你也要加自然景观，喷泉，小岛，瀑布之类的").
NATURAL = ("lake", "waterfall", "plaza", "grove", "meadow", "mountain", "volcano", "peak")
#: Open places with water or track no one stands on (a sea tunnel's sea, a roller coaster's track) - laid as the natural
#: ones are, but not natural: on the road of their life, their own.
LAID = ("sea_tunnel", "roller_coaster")


def span_of(zone: Dict[str, Any]) -> Tuple[int, int]:
    """A building's scale in cells across x down (a place before free placing: from the cells it stood on)."""
    if zone.get("span"):
        return int(zone["span"][0]), int(zone["span"][1])
    cells = zone.get("cells") or [zone.get("cell") or [0, 0]]
    xs, ys = [one[0] for one in cells], [one[1] for one in cells]
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def most_size(span: Tuple[int, int], cell_w: int, cell_h: int, outdoor: bool) -> Tuple[int, int]:
    """The largest floor a block holds: its walls and the way out below inside it, a road's width kept round it."""
    total_w, total_h = span[0] * cell_w - 2, span[1] * cell_h - 1
    if outdoor:
        return total_w - 2, total_h - 3
    return total_w - 2, total_h - 6


def inner_walls(zone: Dict[str, Any]) -> Set[Tuple[int, int]]:
    """THE WALLS INSIDE A BUILDING LARGER THAN A CELL: one down it a third across, one across it half way down -
    each with doorways two wide, so every room is reached. None for one cell, nor outdoors."""
    if zone.get("kind") in OUTDOOR:
        return set()
    left, top, w, h = zone["x"] + 1, zone["y"] + 3, zone["w"], zone["h"]
    out: Set[Tuple[int, int]] = set()
    if w > 16:
        vx = left + w // 3
        gaps = {top + h // 4, top + h // 4 + 1, top + (3 * h) // 4, top + (3 * h) // 4 + 1} if h > 10 else \
            {top + h // 2 - 1, top + h // 2}
        out |= {(vx, y) for y in range(top, top + h) if y not in gaps}
    if h > 10:
        hy = top + h // 2
        gaps = {left + w // 6, left + w // 6 + 1, left + (2 * w) // 3, left + (2 * w) // 3 + 1, left + w // 2,
                left + w // 2 + 1}
        out |= {(x, hy) for x in range(left, left + w) if x not in gaps}
    return out


def inner_doorways(zone: Dict[str, Any]) -> Set[Tuple[int, int]]:
    """The doorways in a building's inner walls (inner_walls' gaps) and a step either side of each - a way kept clear."""
    if zone.get("kind") in OUTDOOR:
        return set()
    left, top, w, h = zone["x"] + 1, zone["y"] + 3, zone["w"], zone["h"]
    walls = inner_walls(zone)
    out: Set[Tuple[int, int]] = set()
    if w > 16:
        vx = left + w // 3
        out |= {(vx + d, y) for y in range(top, top + h) if (vx, y) not in walls for d in (-1, 0, 1)}
    if h > 10:
        hy = top + h // 2
        out |= {(x, hy + d) for x in range(left, left + w) if (x, hy) not in walls for d in (-1, 0, 1)}
    return out


def rooms_of(zone: Dict[str, Any], floor: Tuple[int, int, int, int]) -> List[Tuple[int, int, int, int]]:
    """The rooms inside a building, parted by its inside walls: [left, top, right, bottom) each; the floor whole for one
    cell or outdoors."""
    left, top, w, h = floor
    xs, ys = [left, left + w], [top, top + h]
    if zone.get("kind") not in OUTDOOR:
        if w > 16:
            xs = [left, left + w // 3, left + w // 3 + 1, left + w]
        if h > 10:
            ys = [top, top + h // 2, top + h // 2 + 1, top + h]
    xr = [(xs[0], xs[1])] + ([(xs[2], xs[3])] if len(xs) == 4 else [])
    yr = [(ys[0], ys[1])] + ([(ys[2], ys[3])] if len(ys) == 4 else [])
    return [(a, b, c, d) for a, c in xr for b, d in yr]


def natural_blocked(zone: Dict[str, Any]) -> Set[Tuple[int, int]]:
    """WHAT OF A NATURAL PLACE NOTHING STANDS ON - the same rule as api/web/play-regions.js playNatural: a lake's water
    (an ellipse, less its islet and the bridge to it); a waterfall's rock above, its falls and the pool below; a grove's
    trees (all but a way through its middle and its edge)."""
    kind = zone.get("kind")
    left, top, w, h = zone["x"], zone["y"] + 1, zone["w"], zone["h"]
    out: Set[Tuple[int, int]] = set()
    if kind == "lake":
        cx, cy, rx, ry = left + w / 2, top + h / 2, w / 2 - 1, h / 2 - 1
        for y in range(top, top + h):
            for x in range(left, left + w):
                dx, dy = (x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry
                islet = ((x + 0.5 - cx) / (rx * 0.3)) ** 2 + ((y + 0.5 - cy) / (ry * 0.3)) ** 2 <= 1
                bridge = x == int(cx) and y + 0.5 > cy
                if dx * dx + dy * dy <= 1 and not islet and not bridge:
                    out.add((x, y))
    elif kind == "sea_tunnel":
        mid = top + h // 2
        out |= {(x, y) for y in range(top, top + h) for x in range(left, left + w) if y not in (mid, mid - 1)}
    elif kind == "roller_coaster":
        gate = left + w // 2 - 1                       # its way in, under the track
        out |= {(x, y) for y in range(top, top + h) for x in range(left, left + w)
                if (y in (top, top + h - 1) or x in (left, left + w - 1))
                and not (y == top + h - 1 and x in (gate, gate + 1))}
    elif kind in ("mountain", "volcano", "peak"):
        # A mountain, a volcano, a peak: rock in an ellipse (a peak a narrow one) - not walked on.
        cx, cy = left + w / 2, top + h / 2
        rx, ry = (w / 2 - 0.5, h / 2 - 0.5) if kind != "peak" else (max(1.5, w / 4), h / 2 - 0.5)
        for y in range(top, top + h):
            for x in range(left, left + w):
                if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1:
                    out.add((x, y))
    elif kind == "waterfall":
        cx = left + w // 2
        for y in range(top, top + h):
            for x in range(left, left + w):
                cliff = y < top + 2
                falls = cx - 1 <= x <= cx + 1 and y < top + h - 3
                pool = y >= top + h - 4 and ((x + 0.5 - (left + w / 2)) / (w / 3)) ** 2 + ((y + 0.5 - (top + h - 2)) / 2) ** 2 <= 1
                if cliff or falls or pool:
                    out.add((x, y))
    return out


def pool_water(zone: Dict[str, Any]) -> Set[Tuple[int, int]]:
    """A pool's water: its floor less a walk two wide round it (and a natural place's water and rock)."""
    if zone.get("kind") in ("lake", "waterfall", "mountain", "volcano", "peak", "sea_tunnel", "roller_coaster"):
        return natural_blocked(zone)
    if zone.get("kind") != "pool":
        return set()
    left, top, w, h = zone["x"], zone["y"] + 1, zone["w"], zone["h"]
    if w < 7 or h < 6:
        return set()
    return {(x, y) for x in range(left + 2, left + w - 2) for y in range(top + 2, top + h - 2)}


def _block(origin: Tuple[int, int], span: Tuple[int, int]) -> List[Tuple[int, int]]:
    return [(origin[0] + dx, origin[1] + dy) for dx in range(span[0]) for dy in range(span[1])]


def allowed(cells: Iterable[Tuple[int, int]], region: Dict[str, Any], half: int = 2) -> bool:
    """Within the region, off its middle cell (but the village's home), never across its middle road."""
    cells = list(cells)
    if any(abs(x) > half or abs(y) > half for x, y in cells):
        return False
    if not region.get("starter") and (0, 0) in cells:
        return False
    if len(cells) > 1:
        xs, ys = {x for x, _y in cells}, {y for _x, y in cells}
        # The middle road runs between column 0 and 1, and between row 0 and 1: a block may not have both sides.
        if (0 in xs and 1 in xs) or (0 in ys and 1 in ys):
            return False
    return True


def find_block(world: Dict[str, Any], region: Dict[str, Any], zone: Dict[str, Any],
               span: Tuple[int, int]) -> Optional[List[Tuple[int, int]]]:
    """Cells for `zone` at `span`: holding the cells it has if it can (grown where it stands), else anywhere free in
    its region - nearest the middle first. None when nowhere."""
    mine = {tuple(one) for one in (zone.get("cells") or [zone["cell"]])}
    taken = {tuple(cell) for other in world.get("zones") or [] if other.get("region") == region["id"]
             and other["id"] != zone["id"] for cell in (other.get("cells") or [other["cell"]])}
    candidates = []
    for ox in range(-2, 3):
        for oy in range(-2, 3):
            block = _block((ox, oy), span)
            if not allowed(block, region) or set(block) & taken:
                continue
            keeps = len(mine & set(block))
            candidates.append((-keeps, max(abs(ox), abs(oy)), abs(oy), abs(ox), block))
    if not candidates:
        return None
    candidates.sort()
    best = candidates[0]
    if best[0] == 0 and mine and span == span_of(zone):
        return None                                   # nowhere to grow where it stands: no move for nothing
    return best[-1]


#: NOT ON A GRID (the user, 2026-11-17: "你不要这种网状的"; "重新摆"): a place stands wherever there is room in its region,
#: near its heart or near a place it belongs with, a little off at random, GAP tiles at least from every other (a path's
#: room). Grid cells live on only as a region's size and a building's scale.
CELL_W, CELL_H = 18, 15
GAP = 3
FIRST_INDOOR, FIRST_OUTDOOR = (11, 6), (14, 8)


#: Branches that ended within this many days of the latest are joined again by what comes after them.
LIFE_JOIN_DAYS = 21


def lived(world: Dict[str, Any], zone: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """The days a place comes from: its own (a task's, or those drawn), else those of the growth that made it."""
    days = zone.get("days")
    if isinstance(days, list) and len(days) == 2 and days[0]:
        return str(days[0])[:10], str(days[1] or days[0])[:10]
    for one in world.get("growths") or []:
        if one.get("at") == zone.get("made") and one.get("days"):
            return str(one["days"][0])[:10], str(one["days"][1])[:10]
    return None


def on_the_road(zone: Dict[str, Any]) -> bool:
    """A place the road of their life passes: a place of its own - not a task's grounds, not a natural place, not the
    ground between neighbours."""
    return not zone.get("grounds_of") and zone.get("kind") not in NATURAL and not zone.get("between") \
        and not zone.get("plot") and not zone.get("part_of")


def life_road(world: Dict[str, Any], zones: Optional[List[Dict[str, Any]]] = None) -> Dict[str, List[str]]:
    """THE ORDER OF THEIR LIFE: each place on the road by when it began, and the places it follows - those that had
    ended before it began. Begun while another was still going, it follows what that one followed: the road forks.
    Begun after several had ended, it follows all of them that ended lately: the branches join again. {zone id:
    [the ids it follows]} - none for a beginning. The page lays the same road (api/web/play-regions.js playLifeOf)."""
    from datetime import date
    day = lambda text: date.fromisoformat(text).toordinal()  # noqa: E731
    mains = []
    for zone in zones if zones is not None else world["zones"]:
        days = lived(world, zone)
        if days and on_the_road(zone):
            try:
                mains.append((day(days[0]), day(days[1]), zone["id"]))
            except ValueError:
                continue
    mains.sort()
    follows: Dict[str, List[str]] = {}
    has_next: Set[str] = set()
    done: List[Tuple[int, int, str]] = []
    for start, end, ident in mains:
        before = [one for one in done if one[1] < start]
        if not before:
            follows[ident] = []
        else:
            latest = max(one[1] for one in before)
            ends = [one[2] for one in before if one[2] not in has_next and one[1] >= latest - LIFE_JOIN_DAYS]
            follows[ident] = ends or [max(before, key=lambda one: one[1])[2]]
            has_next.update(follows[ident])
        done.append((start, end, ident))
    return follows


def footprint(kind: str, x: int, y: int, w: int, h: int) -> Tuple[int, int, int, int]:
    """What a place covers, [left, top, right, bottom): a building's walls and the way out of its door below; an open
    place's edge and the row below it."""
    if kind in OUTDOOR:
        return x - 1, y, x + w + 1, y + h + 3
    return x, y, x + w + 2, y + h + 7


def zone_rect(zone: Dict[str, Any]) -> Tuple[int, int, int, int]:
    return footprint(zone["kind"], zone["x"], zone["y"], zone["w"], zone["h"])


def bounds(region: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """Where a region's places may stand: its land, a little in from its edge."""
    from .playregions import BAND, SPAN, spread_of
    ox, oy = region["ox"], region["oy"]
    sx, sy = spread_of(region)                   # a district over several region cells: their land, and the bands between
    return ox - 2 * CELL_W + 1, oy - 2 * CELL_H + 1, ox + 3 * CELL_W - 1 + (sx - 1) * (SPAN * CELL_W + BAND), \
        oy + 3 * CELL_H - 1 + (sy - 1) * (SPAN * CELL_H + BAND)


def square(region: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
    """A region's landmark's paved square, and the row its paths start from (as api/web/play-regions.js lays it)."""
    if region.get("starter") or not region.get("landmark"):
        return None
    cx, cy = region["ox"] + CELL_W // 2 - 1, region["oy"] + CELL_H // 2 - 1
    return cx - 6, cy - 4, cx + 7, region["oy"] + CELL_H


def centre(rect: Tuple[int, int, int, int]) -> Tuple[float, float]:
    return (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2


def heart(world: Dict[str, Any], region: Dict[str, Any]) -> Tuple[float, float]:
    """A region's heart: its landmark's square, or its first place, or the middle of its land."""
    sq = square(region)
    if sq:
        return centre(sq)
    first = next((one for one in world.get("zones") or [] if one.get("region") == region["id"]), None)
    if first:
        return centre(zone_rect(first))
    return region["ox"] + CELL_W / 2, region["oy"] + CELL_H / 2


#: A LAND'S OWN OUTLINE (the user, 2026-11-18: "你现在layout还不是很行，没有脱离矩形" - a world map of islands for what is
#: meant): where a region's places may stand is not its whole rectangle but a round shape of its own within it - its
#: corners off, its sides in and out by three waves whose turn is the region's own - so a land filled is a blob, an
#: island's shape, never a rectangle; the coast (api/web/play-regions.js playLandReach) follows what stands there.
SHAPE_REACH = 1.2                                    # how far out, as a share of the half-widths, the outline reaches
SHAPE_WAVES = ((2, 0.22), (3, 0.16), (5, 0.08))      # each wave round the outline: how many times, how far in and out


#: A land's chosen outline (agent/playshapes.py) reaches this far, in half-widths of its bounds.
CHOSEN_REACH = 1.08


def shape_limit(region: Dict[str, Any], angle: float) -> float:
    """How far the region's outline reaches at this angle from its middle, as a share of its half-widths: its chosen
    outline (a star, a triangle, a circle - the user, 2026-11-19: "每个项目的整片土地"), else its own blob."""
    import hashlib
    import math
    from .playshapes import reach
    chosen = reach(str(region.get("shape") or ""), angle)
    if chosen is not None:
        return CHOSEN_REACH * chosen
    seed = hashlib.sha1(str(region.get("id") or "").encode()).digest()
    return SHAPE_REACH + sum(size * math.sin(times * angle + seed[n] / 255 * 2 * math.pi)
                             for n, (times, size) in enumerate(SHAPE_WAVES))


def within_shape(region: Dict[str, Any], rect: Tuple[int, int, int, int]) -> bool:
    """Whether every corner of `rect` is inside the region's own outline."""
    import math
    b = bounds(region)
    cx, cy, ax, ay = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, (b[2] - b[0]) / 2, (b[3] - b[1]) / 2
    for x, y in ((rect[0], rect[1]), (rect[2], rect[1]), (rect[0], rect[3]), (rect[2], rect[3])):
        dx, dy = (x - cx) / ax, (y - cy) / ay
        if math.hypot(dx, dy) > shape_limit(region, math.atan2(dy, dx)):
            return False
    return True


def _seeded(seed: str, x: int, y: int) -> float:
    import hashlib
    return int(hashlib.sha1(f"{seed}:{x}:{y}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def find_spot(world: Dict[str, Any], region: Dict[str, Any], kind: str, w: int, h: int,
              anchor: Tuple[float, float], seed: str,
              others: Optional[List[Tuple[int, int, int, int]]] = None,
              within: Optional[Tuple[int, int, int, int]] = None) -> Optional[Tuple[int, int]]:
    """WHERE A PLACE OF THIS SIZE MAY STAND: within its region, GAP tiles from every other place and every landmark's
    square, as near `anchor` as it fits - a little off at random (the same each time for the same place), so places
    gather in loose clusters, not rows. Within the land's own outline (within_shape) if there is room there; else
    anywhere in its land - a place is never left out for the outline. None when nowhere."""
    if within is None:
        inside = _find_spot(world, region, kind, w, h, anchor, seed, others, None, shaped=True)
        if inside is not None:
            return inside
    return _find_spot(world, region, kind, w, h, anchor, seed, others, within, shaped=False)


def _find_spot(world: Dict[str, Any], region: Dict[str, Any], kind: str, w: int, h: int,
               anchor: Tuple[float, float], seed: str, others: Optional[List[Tuple[int, int, int, int]]],
               within: Optional[Tuple[int, int, int, int]], shaped: bool) -> Optional[Tuple[int, int]]:
    import math
    b = within or bounds(region)                       # a project's plot: its parts inside it only
    taken = list(others) if others is not None else [zone_rect(one) for one in world.get("zones") or []
                                                      if not (within and one.get("plot"))]
    for one in world.get("regions") or []:
        sq = square(one)
        if sq:
            taken.append(sq)
    grown = [(r[0] - GAP, r[1] - GAP, r[2] + GAP, r[3] + GAP) for r in taken]
    best = None
    for y in range(b[1], b[3]):
        for x in range(b[0], b[2]):
            r = footprint(kind, x, y, w, h)
            if r[0] < b[0] or r[1] < b[1] or r[2] > b[2] or r[3] > b[3]:
                continue
            if any(r[0] < g[2] and r[2] > g[0] and r[1] < g[3] and r[3] > g[1] for g in grown):
                continue
            if shaped and not within_shape(region, r):
                continue                               # outside the land's own outline
            cx, cy = centre(r)
            score = math.hypot(cx - anchor[0], cy - anchor[1]) + _seeded(seed, x // 4, y // 4) * 7
            if best is None or score < best[0]:
                best = (score, x, y)
    return (best[1], best[2]) if best else None


__all__ = ["NATURAL", "natural_blocked", "CELL_W", "CELL_H", "GAP", "FIRST_INDOOR", "FIRST_OUTDOOR", "footprint", "zone_rect", "bounds", "square",
           "centre", "heart", "find_spot", "STYLES", "SCALES", "OUTDOOR", "span_of", "most_size", "inner_walls", "rooms_of", "pool_water", "allowed", "find_block"]

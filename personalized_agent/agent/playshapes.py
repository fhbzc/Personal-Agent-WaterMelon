"""OUTLINES OF ANY SHAPE (the user, 2026-11-19: "不只是地块，每个区域都可以选其他形状，比如五角星，三角形，圆形等" - for
each project's whole land, each open place and each room under a roof).

Each outline is a polygon in a box from -1 to 1 both ways, y down (a star, a triangle, a heart...), or a curve
(an ellipse). Two questions of it, asked the same way here and on the page (api/web/play-regions.js playShapePoly,
playShapeHasPoly, playShapeReach - the same numbers):
- whether a point of the box is inside it (a place's floor, a great ground);
- how far it reaches from its middle at an angle (a land's coast), as a share of the box's half-widths.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

#: The outlines one may choose, each with what it is like.
OUTLINES = {
    "circle": "round - a green, a crater, a ring of standing stones",
    "triangle": "three-sided, a point at the top - a pyramid's ground, a delta",
    "star": "a five-pointed star - a fort, a celebration, a bright idea",
    "star6": "a six-pointed star - a snowflake, a compass, a seal",
    "diamond": "a diamond, a point at each side - a kite, a gem",
    "pentagon": "five-sided - a citadel, a meeting hall",
    "octagon": "eight-sided - a pavilion, a tower's base, a market hall",
    "heart": "a heart - a place loved, a garden of someone",
    "cross": "a cross of four arms - a crossroads, a cloister's walks",
    "trapezoid": "wider below than above - a stage, a terrace, a harbour",
}


def _ring(points: int, first: float = -90.0, radii: Tuple[float, ...] = (1.0,)) -> List[Tuple[float, float]]:
    step = 360.0 / points
    return [(radii[n % len(radii)] * math.cos(math.radians(first + n * step)),
             radii[n % len(radii)] * math.sin(math.radians(first + n * step))) for n in range(points)]


def _heart() -> List[Tuple[float, float]]:
    out = []
    for n in range(32):
        t = n / 32 * 2 * math.pi
        out.append((16 * math.sin(t) ** 3, -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))))
    return out


_RAW = {
    "triangle": _ring(3),
    "star": _ring(10, radii=(1.0, 0.45)),
    "star6": _ring(12, radii=(1.0, 0.58)),
    "diamond": _ring(4),
    "pentagon": _ring(5),
    "octagon": _ring(8, first=-67.5),
    "heart": _heart(),
    "cross": [(-0.34, -1), (0.34, -1), (0.34, -0.34), (1, -0.34), (1, 0.34), (0.34, 0.34), (0.34, 1), (-0.34, 1),
              (-0.34, 0.34), (-1, 0.34), (-1, -0.34), (-0.34, -0.34)],
    "trapezoid": [(-0.55, -1), (0.55, -1), (1, 1), (-1, 1)],
}


def _fill_box(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """The polygon stretched to fill the box from -1 to 1 both ways."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return [(round((x - x0) / (x1 - x0) * 2 - 1, 4), round((y - y0) / (y1 - y0) * 2 - 1, 4)) for x, y in points]


#: Each polygon, filling its box (api/web/play-regions.js PLAY_SHAPE_POLYS: the same points).
POLYGONS: Dict[str, List[Tuple[float, float]]] = {name: _fill_box(points) for name, points in _RAW.items()}


def polygon_has(kind: str, u: float, v: float) -> Optional[bool]:
    """Whether (u, v) in the box from -1 to 1 is inside this outline; None when it is not one of these."""
    if kind == "circle":
        return u * u + v * v <= 1.0
    points = POLYGONS.get(kind)
    if points is None:
        return None
    inside = False
    j = len(points) - 1
    for i, (xi, yi) in enumerate(points):
        xj, yj = points[j]
        if (yi > v) != (yj > v) and u < (xj - xi) * (v - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def reach(kind: str, angle: float) -> Optional[float]:
    """How far this outline reaches from the box's middle at `angle` (radians, y down), in half-widths; None when it is
    not one of these. The farthest edge the ray from the middle crosses."""
    if kind == "circle":
        return 1.0
    points = POLYGONS.get(kind)
    if points is None:
        return None
    dx, dy = math.cos(angle), math.sin(angle)
    best = 0.0
    j = len(points) - 1
    for i, (xi, yi) in enumerate(points):
        xj, yj = points[j]
        ex, ey = xj - xi, yj - yi
        den = dx * ey - dy * ex
        if abs(den) > 1e-9:
            t = (xi * ey - yi * ex) / den                # along the ray
            s = (xi * dy - yi * dx) / den                # along the edge
            if t > 0 and 0 <= s <= 1:
                best = max(best, t)
        j = i
    return best or 0.05


__all__ = ["OUTLINES", "POLYGONS", "polygon_has", "reach"]

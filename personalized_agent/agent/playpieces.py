"""GREAT PIECES (the user, 2026-11-19: "让模型可以做一个大件design ... 模型先判断是否要做大件design, 确定之后给模型一个描述，然后
进入大件design 的call, 然后用类似过山车的构筑方法做"; "要告诉模型，milestone, 特殊东西最好都做大件design"; "数量要根据面积挂钩";
"都允许" - rooms and open places alike; "允许交互").

How: a place's design (the room design, a great ground's open ground) may ask for great pieces ("pieces": what it is,
which kit, where, for which card or stage) - as many as its floor allows (pieces_allowed). Each is then designed in a
call of its own (play_world_piece): an assembly of the kit's parts, as the roller coaster is built - a path and its
heights for a track, segments stacked for a tower, a grid of a widget for a field, an outline for water, shapes laid
together for a machine. The code keeps it to what can be (read_piece), the page builds it (play.js playPiecePicture)
- drawn once, its moving parts each frame - and it is drawn here as a sketch to be looked at once (draw_piece,
play_world_piece_look). How far it is built is the work's, as a widget's: a tower up to where its card has come, the
rest scaffolding; a track laid so far; a field ripening.

Interaction: a track is ridden, a stack climbed (the view from its top), anything else looked at (its card).
"""

from __future__ import annotations

import base64
import io
import re
from typing import Any, Dict, List, Optional, Tuple

TASK_PIECE = "play_world_piece"
TASK_PIECE_LOOK = "play_world_piece_look"
#: A place may have one great piece for each this many tiles of its floor - one at least, PIECES_MOST at most.
PIECE_TILES, PIECES_MOST = 160, 6
#: A piece at least this large, at most a share of its place's floor.
PIECE_LEAST, PIECE_SHARE_MOST = (3, 3), 0.5
HEIGHT_MOST = 8

KITS = {
    "track": "a ride on a track - a roller coaster, a water flume, a mine train, a slide: its path round its ground and "
             "how high each stretch is; ridden",
    "stack": "a thing built up in segments - a tower, a rocket, a lighthouse, a clock tower, a skyscraper: its segments "
             "from the ground up; climbed",
    "field": "a field of one widget in rows - a melon field, an orchard, beehives, solar panels: the widget, its rows and "
             "columns, a path, a fence",
    "water": "water as a feature - a lake, a canal, a fountain pool: its outline, bridges, boats, a fountain",
    "composite": "a machine or a monument of shapes laid together - a factory, a satellite dish, a launch gantry, a "
                 "statue: rectangles, circles, triangles, cylinders, some turning, blinking, smoking",
}
TRACK_ELEMENTS = ("station", "flat", "lift", "drop", "hill", "loop", "helix", "brake")
STACK_SEGMENTS = ("base", "body", "windows", "balcony", "roof", "spire", "dome", "fins", "nozzle", "antenna", "lantern",
                  "clock", "battlement")
SHAPES = ("rect", "circle", "triangle", "cylinder")
MOVES = ("none", "spin", "blink", "smoke")
WATER_OUTLINES = ("ellipse", "blob", "band", "rect", "ring")
INTERACT = {"track": "ride", "stack": "climb"}


def pieces_allowed(floor_tiles: int) -> int:
    """How many great pieces a place of this floor may have (the user: "数量要根据面积挂钩")."""
    return max(1, min(PIECES_MOST, floor_tiles // PIECE_TILES))


ROOM_PIECES_NOTE = """GREAT PIECES ("pieces", at most "pieces_most" here): something too great for one thing - a roller coaster, \
a tower, a rocket, a field, a lake, a machine - built of parts in a design of its own after this one. MILESTONES, \
A STAGE'S RESULT AND ANYTHING SPECIAL ARE BEST A GREAT PIECE. For each say what it is ("what": a few sentences - what \
it looks like, what it means), its kit ({kits}), where it stands ("x", "y", "w", "h": on "." tiles of the grid, \
{least} at least, kept clear of the other things) and the card (or the great ground's "stage") it stands for. ONE OF \
THEIR PICTURES MAY BE ONE (the user, 2026-11-19: "对图片的处理是可以直接放，也可以用像素风拼出某个图片（交给大item design之类)"): \
hung as it is (a frame, a poster), or BUILT AFTER IT as a great piece - "picture": "<its id>" - its shapes and colours \
rebuilt in the world's pixel style, a monument of it. A ROOM \
MAY HAVE ONE TOO, AS LARGE AS ITS FLOOR ALLOWS: the size limit of the things above is for things, not for great \
pieces. A great piece is only ever asked for here, never laid as a thing. None when nothing calls for one."""

PIECE_SYSTEM = """You are the builder of ONE GREAT PIECE in a small world that pictures the user's life - built of \
parts the way a roller coaster is built of its track: "what" says what it is and what it means; its ground is "w" x \
"h" tiles; its kit is "{kit}" ({kit_what}). It stands for a piece of their work ("work": its title, status, how far \
it has come) - the code builds as much of it as the work has come, the rest shown under way (scaffolding, track still \
to lay, a field still growing). Make it unforgettable, in the place's own look ("place").
Answer with only JSON for the kit:
{form}
and "name": "<a few words>", "why": "<to them, at most 2 sentences>"."""

KIT_FORMS = {
    "track": '"points": [[x, y], ... 4 to 24 points round its ground, in tiles from its top left, a closed loop], '
             '"elements": [{"at": <the point it starts at>, "kind": "<' + "|".join(TRACK_ELEMENTS) + '>", "height": 0 to '
             + str(HEIGHT_MOST) + '}, ...], "colour": "#rrggbb", "cars": 1 to 6',
    "stack": '"segments": [{"kind": "<' + "|".join(STACK_SEGMENTS) + '>", "height": 0.5 to 4 (tiles), "width": 0.2 to 1 '
             '(of its ground\'s width), "colour": "#rrggbb"}, ... from the ground up, 2 to 14], "door": true or false',
    "field": '"widget": "<a widget name: field_<crop>, orchard, apiary, vineyard, greenhouse, chicken_farm, '
             'fish_pond>", "rows": 1 to 6, "cols": 1 to 6, "path": true or false, "fence": "#rrggbb" or ""',
    "water": '"outline": "<' + "|".join(WATER_OUTLINES) + '>", "bridges": 0 to 3, "boats": 0 to 4, "fountain": true or '
             'false, "colour": "#rrggbb"',
    "composite": '"shapes": [{"kind": "<' + "|".join(SHAPES) + '>", "x": <tiles from its left>, "y": <tiles from its '
                 'top>, "w": <tiles>, "h": <tiles>, "up": <how high off the ground, tiles>, "colour": "#rrggbb", '
                 '"moves": "<' + "|".join(MOVES) + '>"}, ... 1 to 24, the first underneath]',
}

PIECE_LOOK_SYSTEM = """You built this great piece (your design is given) - here it is sketched. Look at it once as its \
builder would: is it what was meant, does it read from afar, is it too plain or too busy, do its colours hold together. \
Then give it again with that put right, in the same JSON, and "looked": "<what you changed, at most 2 sentences>"."""


def piece_system(kit: str) -> str:
    return PIECE_SYSTEM.format(kit=kit, kit_what=KITS[kit], form=KIT_FORMS[kit])


def room_pieces_note() -> str:
    return ROOM_PIECES_NOTE.format(kits="; ".join(f"{k}: {v}" for k, v in KITS.items()),
                                   least=f"{PIECE_LEAST[0]} x {PIECE_LEAST[1]}")


def _colour(value: Any, fallback: str) -> str:
    value = str(value or "")
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback


def _number(value: Any, least: float, most: float, fallback: float) -> float:
    try:
        return max(least, min(most, float(value)))
    except (TypeError, ValueError):
        return fallback


def read_asked(said: Any, rows: List[str], left: int, top: int, claimed: set, most: int) -> List[Dict[str, Any]]:
    """THE PIECES A PLACE'S DESIGN ASKED FOR, KEPT TO WHAT CAN BE: a kit there is, on its floor's free tiles (not a wall,
    not the way in, not what the design put there), as large as PIECE_LEAST, at most PIECE_SHARE_MOST of the floor,
    clear of one another - `most` at most."""
    if not isinstance(said, dict):
        return []
    free = {(x, y) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch == "."} - set(claimed)
    share = len([ch for row in rows for ch in row if ch in ".X"]) * PIECE_SHARE_MOST
    out: List[Dict[str, Any]] = []
    taken: set = set()
    short = lambda text, n: " ".join(str(text or "").split())[:n]       # noqa: E731
    for one in said.get("pieces") or []:
        if not isinstance(one, dict) or one.get("kit") not in KITS or len(out) >= most:
            continue
        try:
            x, y, w, h = (int(one.get(k)) for k in ("x", "y", "w", "h"))
        except (TypeError, ValueError):
            continue
        tiles = {(x + dx, y + dy) for dx in range(w) for dy in range(h)}
        if w < PIECE_LEAST[0] or h < PIECE_LEAST[1] or w * h > share or not tiles <= free or tiles & taken:
            continue
        taken |= tiles
        out.append({"kit": one["kit"], "what": short(one.get("what"), 600), "x": left + x, "y": top + y, "w": w, "h": h,
                    **({"picture": short(one.get("picture"), 120)} if str(one.get("picture") or "").strip() else {}),
                    **({"card": short(one.get("card"), 80)} if str(one.get("card") or "").strip() else {}),
                    **({"stage": one["stage"]} if isinstance(one.get("stage"), int) else {})})
    return out


#: One of their pictures as the piece's designer is shown it: at most this wide or high.
PHOTO_MOST = 768


def photo_block(path: Any) -> Optional[Dict[str, str]]:
    """ONE OF THEIR PICTURES, FOR A PIECE BUILT AFTER IT: made small, as PNG - {"media_type", "data"}; None when it
    cannot be read."""
    try:
        from PIL import Image
        with Image.open(str(path)) as image:
            image = image.convert("RGB")
            image.thumbnail((PHOTO_MOST, PHOTO_MOST))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
    except Exception:
        return None
    return {"media_type": "image/png", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}


def read_piece(found: Any, kit: str, w: int, h: int) -> Optional[Dict[str, Any]]:
    """A PIECE AS BUILT, KEPT TO WHAT CAN BE (the code's): its parts of its kit only, inside its ground, as high as
    HEIGHT_MOST; a track a closed loop of four points or more; a stack of two segments or more; a field of a widget
    that is; shapes two dozen at most. None when nothing of it can be built."""
    from .playwidgets import WIDGETS
    if not isinstance(found, dict):
        return None
    short = lambda text, n: " ".join(str(text or "").split())[:n]       # noqa: E731
    out: Dict[str, Any] = {"name": short(found.get("name"), 60), "why": short(found.get("why"), 400)}
    if kit == "track":
        points = []
        for one in (found.get("points") or [])[:24]:
            try:
                points.append([max(0.5, min(w - 0.5, float(one[0]))), max(0.5, min(h - 0.5, float(one[1])))])
            except (TypeError, ValueError, IndexError):
                continue
        if len(points) < 4:
            return None
        elements = []
        for one in found.get("elements") or []:
            if isinstance(one, dict) and one.get("kind") in TRACK_ELEMENTS:
                at = int(_number(one.get("at"), 0, len(points) - 1, 0))
                elements.append({"at": at, "kind": one["kind"], "height": _number(one.get("height"), 0, HEIGHT_MOST, 1)})
        if not any(e["kind"] == "station" for e in elements):
            elements.insert(0, {"at": 0, "kind": "station", "height": 0.3})
        out.update(points=points, elements=sorted(elements, key=lambda e: e["at"]),
                   colour=_colour(found.get("colour"), "#d9382f"), cars=int(_number(found.get("cars"), 1, 6, 3)))
    elif kit == "stack":
        segments = []
        for one in (found.get("segments") or [])[:14]:
            if isinstance(one, dict) and one.get("kind") in STACK_SEGMENTS:
                segments.append({"kind": one["kind"], "height": _number(one.get("height"), 0.5, 4, 1),
                                 "width": _number(one.get("width"), 0.2, 1, 0.7), "colour": _colour(one.get("colour"), "#c9c4b8")})
        total = sum(s["height"] for s in segments)
        if len(segments) < 2:
            return None
        if total > HEIGHT_MOST + h:                       # too tall: every segment made shorter alike
            for s in segments:
                s["height"] = round(s["height"] * (HEIGHT_MOST + h) / total, 2)
        out.update(segments=segments, door=bool(found.get("door", True)))
    elif kit == "field":
        widget = str(found.get("widget") or "")
        if widget not in WIDGETS:
            return None
        out.update(widget=widget, rows=int(_number(found.get("rows"), 1, 6, 2)), cols=int(_number(found.get("cols"), 1, 6, 2)),
                   path=bool(found.get("path")), fence=_colour(found.get("fence"), "") if found.get("fence") else "")
    elif kit == "water":
        outline = str(found.get("outline") or "")
        out.update(outline=outline if outline in WATER_OUTLINES else "blob",
                   bridges=int(_number(found.get("bridges"), 0, 3, 1)), boats=int(_number(found.get("boats"), 0, 4, 1)),
                   fountain=bool(found.get("fountain")), colour=_colour(found.get("colour"), "#3e8fc9"))
    elif kit == "composite":
        shapes = []
        for one in (found.get("shapes") or [])[:24]:
            if not isinstance(one, dict) or one.get("kind") not in SHAPES:
                continue
            sx, sy = _number(one.get("x"), 0, w, 0), _number(one.get("y"), 0, h, 0)
            shapes.append({"kind": one["kind"], "x": sx, "y": sy, "w": _number(one.get("w"), 0.2, w - sx or 0.2, 1),
                           "h": _number(one.get("h"), 0.2, h + HEIGHT_MOST, 1), "up": _number(one.get("up"), 0, HEIGHT_MOST, 0),
                           "colour": _colour(one.get("colour"), "#9aa3ad"),
                           "moves": one.get("moves") if one.get("moves") in MOVES else "none"})
        if not shapes:
            return None
        out.update(shapes=shapes)
    else:
        return None
    return out


def draw_piece(kit: str, spec: Dict[str, Any], w: int, h: int) -> Optional[Dict[str, str]]:
    """A SKETCH OF THE PIECE, for its builder to look at (play_world_piece_look): its ground, and its parts as the page
    builds them in outline - the track's path raised by its heights, the stack's segments, the field's rows, the water,
    the shapes. {"media_type", "data"}, or None."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    T = 16
    up = HEIGHT_MOST + 4
    image = Image.new("RGB", (w * T, (h + up) * T), (110, 150, 80))
    d = ImageDraw.Draw(image)
    d.rectangle([0, up * T, w * T - 1, (h + up) * T - 1], fill=(150, 130, 95), outline=(90, 70, 50))
    hexrgb = lambda c: tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) if c else (150, 150, 150)   # noqa: E731
    if kit == "track":
        heights = [0.0] * len(spec["points"])
        for e in spec["elements"]:
            for i in range(e["at"], len(heights)):
                heights[i] = e["height"]
        pts = [(x * T, (y + up - hh) * T) for (x, y), hh in zip(spec["points"], heights)]
        for (x, y), hh in zip(spec["points"], heights):
            d.line([x * T, (y + up) * T, x * T, (y + up - hh) * T], fill=(120, 125, 135), width=2)
        d.line(pts + pts[:1], fill=hexrgb(spec["colour"]), width=4)
    elif kit == "stack":
        base = (h + up) * T - 4
        for s in spec["segments"]:
            sw = s["width"] * w * T
            d.rectangle([w * T / 2 - sw / 2, base - s["height"] * T, w * T / 2 + sw / 2, base], fill=hexrgb(s["colour"]),
                        outline=(40, 40, 40))
            base -= s["height"] * T
    elif kit == "field":
        for r in range(spec["rows"]):
            for c in range(spec["cols"]):
                cw, ch = w * T / spec["cols"], h * T / spec["rows"]
                d.rectangle([c * cw + 2, up * T + r * ch + 2, (c + 1) * cw - 2, up * T + (r + 1) * ch - 2], fill=(90, 140, 60))
    elif kit == "water":
        d.ellipse([T, up * T + T, w * T - T, (h + up) * T - T], fill=hexrgb(spec["colour"]))
    elif kit == "composite":
        for s in spec["shapes"]:
            x0, y0 = s["x"] * T, (s["y"] + up - s["up"]) * T
            box = [x0, y0 - s["h"] * T + T, x0 + s["w"] * T, y0 + T]
            fill = hexrgb(s["colour"])
            if s["kind"] == "circle":
                d.ellipse(box, fill=fill, outline=(40, 40, 40))
            elif s["kind"] == "triangle":
                d.polygon([(box[0], box[3]), ((box[0] + box[2]) / 2, box[1]), (box[2], box[3])], fill=fill, outline=(40, 40, 40))
            else:
                d.rectangle(box, fill=fill, outline=(40, 40, 40))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return {"media_type": "image/png", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}


# -- DRAWN FIRST, THEN BUILT AFTER ITS PICTURE ------------------------------------------------------------------------
#: The picture's canvas: 16 units a tile; its ground the last "h" rows of tiles, the UP_TILES above it the air it rises in.
UNIT, UP_TILES = 16, HEIGHT_MOST + 4
SVG_MOST, SVG_ELEMENTS_MOST, SVG_STAGES_MOST = 90000, 3000, 8
SVG_TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "defs", "linearGradient",
            "radialGradient", "stop", "title", "desc"}
SVG_ATTRS = {"d", "x", "y", "width", "height", "cx", "cy", "r", "rx", "ry", "points", "x1", "y1", "x2", "y2", "fill",
             "stroke", "stroke-width", "opacity", "fill-opacity", "stroke-opacity", "transform", "id", "class",
             "data-stage", "data-cx", "data-cy", "offset", "stop-color", "stop-opacity", "gradientUnits",
             "gradientTransform", "fx", "fy", "stroke-linecap", "stroke-linejoin", "fill-rule", "stroke-dasharray",
             "viewBox", "xmlns"}
MOVES_CLASSES = ("spin", "blink", "smoke")

PIECE_SVG_SYSTEM = """You are the artist and builder of ONE GREAT PIECE in a small world that pictures the user's life - \
a 2D pixel world seen from the front and a little above, as in a classic adventure game. "what" says what it is and \
what it means; it stands for a piece of their work ("work"). FIRST DRAW IT, AS A PICTURE (SVG), richly - it is then \
built after your picture, and shown as fine pixel art (each tile 64 x 64 pixels): layers, light from the top left, \
dark outlines, textures, small details that reward a close look, in the place's own colours ("place").
ITS CANVAS: viewBox "0 0 {width} {height}" - {unit} units a tile; its GROUND is the bottom {h} tiles (y from \
{ground} to {height}): what stands on it stands there; it may rise into the {up} tiles of air above.
WHAT THE BUILDING READS FROM THE PICTURE (give each where it applies):
- id="entrance": a rect on the bottom edge of its ground, two tiles wide - the way in (a building, a ride).
- id="track": one closed path the ride's cars run round (a roller coaster, a train ride); it is ridden.
- class="spin" (with data-cx, data-cy: the point it turns round), class="blink", class="smoke": its moving parts.
- data-stage="1" ... "{stages}" on its groups, IN THE ORDER IT IS BUILT - the code shows as much as the work has come, \
the rest under scaffolding.
Only shapes, paths, groups and gradients: no text, no images, no scripts, no filters, nothing from outside.
Answer with only JSON: {{"svg": "<svg ...>...</svg>", "name": "<a few words>", "why": "<to them, at most 2 sentences>"}}"""

PIECE_SVG_LOOK_SYSTEM = """You drew this great piece (your picture is given, and here it is as it is drawn). Look at it \
once as its artist would: does it read as what was meant from afar and reward a close look, is it too plain or too \
busy, do its colours hold together, does it stand on its ground. Then give it again with that put right, in the same \
JSON, and "looked": "<what you changed, at most 2 sentences>"."""


def piece_svg_system(w: int, h: int) -> str:
    width, height = w * UNIT, (h + UP_TILES) * UNIT
    return PIECE_SVG_SYSTEM.format(width=width, height=height, unit=UNIT, h=h, ground=UP_TILES * UNIT, up=UP_TILES,
                                   stages=SVG_STAGES_MOST)


def clean_svg(text: Any, width: int, height: int) -> Optional[Any]:
    """A PICTURE KEPT TO WHAT IS SAFE: the parsed SVG of `text` with only SVG_TAGS and SVG_ATTRS, no reference outside
    it, at most SVG_MOST characters and SVG_ELEMENTS_MOST elements, its canvas `width` x `height` - or None."""
    return read_svg({"svg": text}, width / UNIT, height / UNIT - UP_TILES, tree_only=True)


def read_svg(found: Any, w: Any, h: Any, tree_only: bool = False) -> Optional[Any]:
    """THE PICTURE KEPT TO WHAT IS SAFE AND CAN BE BUILT (the code's): only the shapes, groups and gradients of SVG_TAGS
    and the attributes of SVG_ATTRS (a gradient's fill by its own "url(#...)" only), SVG_MOST characters and
    SVG_ELEMENTS_MOST elements at most, its canvas set to the piece's; what it says of itself read - its entrance's
    tiles, whether it has a track, how many stages, which parts move. None when there is no picture to keep."""
    import xml.etree.ElementTree as ET
    if not isinstance(found, dict):
        return None
    text = str(found.get("svg") or "").strip()
    if not text or len(text) > SVG_MOST:
        return None
    try:
        tree = ET.fromstring(text)
    except ET.ParseError:
        return None
    count = 0

    def bare(tag: str) -> str:
        return tag.split("}", 1)[-1]

    def clean(node: Any) -> bool:
        nonlocal count
        count += 1
        if count > SVG_ELEMENTS_MOST or bare(node.tag) not in SVG_TAGS:
            return False
        node.tag = bare(node.tag)
        for key in list(node.attrib):
            name = bare(key)
            value = str(node.attrib.pop(key))
            if name not in SVG_ATTRS:
                continue
            if "url(" in value and not re.fullmatch(r"\s*url\(#[\w-]+\)\s*", value):
                continue                                 # nothing from outside
            if re.search(r"javascript:|expression\(|@import|<", value, re.I):
                continue
            node.set(name, value[:20000])
        node.text = None if bare(node.tag) not in ("title", "desc") else (node.text or "")[:200]
        for child in list(node):
            if not clean(child):
                node.remove(child)
            child.tail = None
        return True
    if bare(tree.tag) != "svg" or not clean(tree):
        return None
    width, height = round(w * UNIT), round((h + UP_TILES) * UNIT)
    tree.attrib.update({"xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 {width} {height}",
                        "width": str(width), "height": str(height)})
    if tree_only:
        return ET.tostring(tree, encoding="unicode") if count >= 2 else None
    stages = set()
    entrance, track, moving = None, False, 0
    for node in tree.iter():
        if node.get("data-stage", "").isdigit():
            stages.add(min(SVG_STAGES_MOST, int(node.get("data-stage"))))
        if node.get("id") == "entrance" and node.tag == "rect":
            try:
                x0 = float(node.get("x") or 0) / UNIT
                entrance = [max(0, min(w - 1, int(x0))), max(0, min(w - 1, int(x0 + float(node.get("width") or 32) / UNIT) - 1))]
            except ValueError:
                entrance = None
        if node.get("id") == "track" and node.tag == "path" and node.get("d"):
            track = True
        if any(c in (node.get("class") or "").split() for c in MOVES_CLASSES):
            moving += 1
    svg = ET.tostring(tree, encoding="unicode")
    if count < 3:
        return None                                       # nothing drawn
    short = lambda value, n: " ".join(str(value or "").split())[:n]     # noqa: E731
    return {"svg": svg, "stages": len(stages), "entrance": entrance, "track": track, "moving": moving,
            "name": short(found.get("name"), 60), "why": short(found.get("why"), 400)}


def render_svg(svg: str, w: int, h: int, scale: float = 2.0) -> Optional[Dict[str, str]]:
    """THE PICTURE AS IT IS DRAWN, for its artist to look at (play_world_piece_look): on a ground of grass, its own
    canvas - by svglib (to PDF) and pdfium (to a picture), both of this application already. {"media_type", "data"}."""
    try:
        from PIL import Image
        from reportlab.graphics import renderPDF
        from svglib.svglib import svg2rlg
        import pypdfium2 as pdfium
    except Exception:
        return None
    try:
        drawing = svg2rlg(io.BytesIO(svg.encode("utf-8")))
        if drawing is None:
            return None
        page = pdfium.PdfDocument(renderPDF.drawToString(drawing))[0]
        picture = page.render(scale=scale * 4 / 3).to_pil().convert("RGB")
    except Exception:
        return None
    width, height = int(w * UNIT * scale), int((h + UP_TILES) * UNIT * scale)
    picture = picture.resize((width, height))
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    return {"media_type": "image/png", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}


__all__ = ["clean_svg", "PIECE_SVG_SYSTEM", "PIECE_SVG_LOOK_SYSTEM", "piece_svg_system", "read_svg", "render_svg", "UNIT", "UP_TILES",
           "TASK_PIECE", "TASK_PIECE_LOOK", "KITS", "pieces_allowed", "room_pieces_note", "piece_system",
           "PIECE_LOOK_SYSTEM", "read_asked", "read_piece", "draw_piece", "INTERACT", "HEIGHT_MOST"]

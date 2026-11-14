"""Watermelon: the first avatar - a watermelon on a bed of leaves, its face
to the left, whose eyes follow the pointer.

Drawn here, from shapes, rather than kept as pictures: the body and the
leaves are painted once (a matte pastel melon with teal longitudinal stripes, a curled
vine on top, and mint leaf feet under it), and the face is
drawn on them every frame - so the pupils can look where the pointer is, the
eyes can blink, and the face can answer what the agent is doing:

* idle - breathing slowly, blinking now and then, eyes on the pointer;
* hover - eyes closed in a smile;
* a click - a hop and a squash;
* talking - the mouth opening and closing;
* thinking - eyes up, a little row of thought bubbles;
* notification - brows up, cheeks pink, a red "!" and a hop.

The desktop window draws it with `Watermelon.frame`; the settings page
draws the same face in SVG over the same body and leaves, from `RIG`.
"""

from __future__ import annotations

import base64
import copy
import io
import json
import math
from pathlib import Path
from typing import Any, Dict, Tuple

NAME = "watermelon_flat"
#: What the agent is called with it, as with the first one; LABEL is how the avatar list names it.
DISPLAY_NAME = "Watermelon"
LABEL = "Flat watermelon"
STATES = ("idle", "talking", "thinking", "notification")

OUTLINE = (67, 127, 119)
LEAF = (153, 210, 192)
LEAF_DARK = (130, 185, 187)
LEAF_VEIN = (71, 143, 145)
BASE = (174, 213, 164)
STRIPE = (119, 180, 169)
CREAM = (255, 255, 255)
IRIS = (113, 135, 113)
PUPIL = (83, 105, 88)
INK = (106, 129, 108)
BROW = (119, 140, 117)
BLUSH = (240, 120, 120)
PINK = (236, 175, 183)
TEAR = (176, 226, 250)

#: The body, in a 128-unit box: centre, half length and half height, its
#: tilt (the left end a little higher - it faces left) and how square its
#: ends are.
CX, CY, A, B, TILT, P = 66.0, 59.0, 47.0, 41.0, math.radians(0), 2.0
#: The leaves under it, a bed of them: where each grows from, its direction
#: (degrees, 0 to the right, 90 down), its length and its half width.
LEAVES = ((49, 96, 156, 39, 15), (79, 96, 24, 39, 15),
          (48, 98, 198, 39, 14), (80, 98, -18, 39, 14))
#: The face, to the left: each eye's centre, half width, half height and iris
#: radius - the near one larger - and the mouth.
EYES = ((79.0, 58.0, 8.4, 10.4, 6.6), (47.0, 60.0, 8.0, 10.0, 6.3))
MOUTH = (63.0, 78.0)
#: The cheeks, the "!" of a notification and the thought bubbles - by the face.
CHEEKS = ((88, 74), (37, 75))
BADGE = (22.0, 16.0, 10.0)
SWEAT = (78.0, 42.0)
BUBBLES = ((36, 24, 2.2), (28, 16, 3), (18, 7, 3.8))
#: Where the body rests: it squashes and hops from here.
GROUND = CY + B
#: Stood on end, how far across the 128 units the middle of its face is.
TURNED_FACE_X = 50
#: Upside down out of the top, how far down the middle of its face is.
HANGING_FACE_Y = 78

#: How far the body goes off the leaves when tucked into the side of the
#: screen: past what stays out (ui/avatar_tuck.py), only a leaf left.
TUCK_SHIFT = 30
#: How far past the side of the screen the leaves holding on to it go.
GRIP_PAST = 4
#: PARTED (the user, 2026-11-05: "感觉是拨开叶子丛出现的"): its head coming out, each half of the
#: bed goes aside by this much and swings by this many degrees, its tips round the head.
PART_APART, PART_SWING = 10.0, 24.0
#: Where each half of the bed turns from: its stems.
LEFT_STEMS, RIGHT_STEMS = (50.0, 97.0), (78.0, 97.0)

#: What the page needs to draw the same face (api: GET /api/characters).
RIG = {"kind": "watermelon", "variant": "flat", "box": 128, "face_scale": 1.0, "face_origin": [63, 66], "eyes": [list(one) for one in EYES], "mouth": list(MOUTH),
       "ground": GROUND, "cheeks": [list(one) for one in CHEEKS], "badge": list(BADGE),
       "bubbles": [list(one) for one in BUBBLES], "brow_arc": [220, 340], "brow_shift": 1, "brow_shape": "peaked", "brow_drop": 3,
       "looks_from": [63, 59], "faces": "left",
       "colours": {"outline": "#%02x%02x%02x" % OUTLINE, "cream": "#%02x%02x%02x" % CREAM,
                   "iris": "#%02x%02x%02x" % IRIS, "pupil": "#%02x%02x%02x" % PUPIL,
                   "ink": "#%02x%02x%02x" % INK, "brow": "#%02x%02x%02x" % BROW,
                   "mouth_inner": "#bc8998", "tongue": "#e6b3bf",
                   "blush": "#%02x%02x%02x" % BLUSH}}


def _light_masks(u: Any, v: Any) -> tuple:
    """One upper-left light direction and falloff for all painted surfaces."""
    import numpy as np
    direction = .55 * u + .8 * v
    shadow = np.clip((direction - .30) / .80, 0, 1)
    shadow = shadow * shadow * (3 - 2 * shadow) * .54
    highlight = np.clip((-direction - .35) / .9, 0, 1)
    highlight = highlight * highlight * (3 - 2 * highlight) * .65
    return shadow, highlight


# -- the body and the leaves, painted once ------------------------------------
def _body(scale: int) -> Any:
    """Matte painted melon and attached vine; the live face stays separate."""
    import numpy as np
    from PIL import Image, ImageDraw
    n = 128 * scale
    ys, xs = np.mgrid[0:n, 0:n].astype(np.float32) / scale
    u, v = (xs - CX) / A, (ys - CY) / B
    radius = np.abs(u) ** P + np.abs(v) ** P
    # Uneven growing bands follow the rind, tapering and meandering independently.
    longitude = u / np.sqrt(np.clip(1 - (v * .85) ** 2, .2, 1))
    stripes = np.zeros((n, n), dtype=bool)
    for i, (centre, width) in enumerate(((-.79, .084), (-.27, .100), (.27, .089), (.78, .076))):
        phase = i * 1.73
        wave = (.048 * np.sin(v * 3.7 + phase)
                + .021 * np.sin(v * 8.3 + phase * 1.4)
                + .008 * np.sin(v * 19.1 + phase * .7))
        taper = .76 + .24 * np.sqrt(np.clip(1 - v * v, 0, 1))
        varying_width = width * taper * (1 + .22 * np.sin(v * 6.2 + phase))
        edge = .006 * np.sin(v * 32 + u * 17 + phase)
        stripes |= np.abs(longitude - centre - wave) < varying_width + edge
    rng = np.random.default_rng(12)
    grain = np.repeat(np.repeat(rng.normal(0, 3.6, (128, 128, 1)), scale, axis=0), scale, axis=1)
    colour = (np.array(BASE) * (1 - stripes[..., None]) + np.array(STRIPE) * stripes[..., None])
    # A cool painted wash follows the lower-right curve; add grain afterwards
    # so the shadow retains the same brush texture as the lit surface.
    shadow, highlight = _light_masks(u, v)
    blue = (np.array((116, 164, 194)) * (1 - stripes[..., None]) +
            np.array((81, 135, 165)) * stripes[..., None])
    colour = colour * (1 - shadow[..., None]) + blue * shadow[..., None]
    colour = colour * (1 - highlight[..., None]) + np.array((255, 245, 102)) * highlight[..., None]
    # Keep a green rind above the left leaf, distinct from its yellow light.
    left = np.clip((-u - .05) / .45, 0, 1)
    lower = np.clip((v - .15) / .40, 0, 1)
    green_edge = left * left * (3 - 2 * left) * lower * lower * (3 - 2 * lower)
    green = (np.array((164, 203, 151)) * (1 - stripes[..., None]) + np.array(STRIPE) * stripes[..., None])
    colour = colour * (1 - green_edge[..., None]) + green * green_edge[..., None] + grain
    rgba = np.zeros((n, n, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(colour, 0, 255).astype(np.uint8)
    rgba[..., 3] = (radius <= 1) * 255
    image = Image.fromarray(rgba, "RGBA")
    draw = ImageDraw.Draw(image)
    # The curl belongs to the body layer so every hop/turn carries it along.
    points = [(66, 25), (65, 21), (67, 17), (72, 15), (77, 16),
              (79, 12), (77, 8), (72, 8), (70, 11), (72, 13), (74, 12)]
    draw.line([(int(x * scale), int(y * scale)) for x, y in points],
              fill=STRIPE + (255,), width=max(1, int(2.1 * scale)), joint="curve")
    draw.polygon([(63 * scale, 20 * scale), (55 * scale, 13 * scale),
                  (56 * scale, 20 * scale), (63 * scale, 23 * scale)], fill=LEAF + (255,))
    return _brush_texture(image, scale)


def _brush_texture(image: Any, scale: int, strength: float = 1.0) -> Any:
    """Fixed paper grain at logical resolution, so it survives downsampling."""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(28)
    grain = rng.normal(0, 3.8, (128, 128, 1))
    grain = np.repeat(np.repeat(grain, scale, axis=0), scale, axis=1)
    pixels = np.array(image)
    pixels[..., :3] = np.clip(pixels[..., :3].astype(float) + grain * strength, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels, "RGBA")


def _blade(t: float, half: float) -> float:
    """A leaf's half width along it: broad near where it grows from, pointed
    at the tip, the edge gently waving."""
    shape = math.sin(math.pi * min(1.0, t) ** 0.75) * (1 - 0.25 * t)
    return half * shape * (1 + 0.07 * math.sin(t * 6.5 * math.pi))


def _leaf(draw: Any, bx: float, by: float, angle: float, length: float, half: float,
          scale: int) -> None:
    """One leaf: its blade, the half turned from the light darker, the outline,
    the midrib and the side veins; its tip curling up a little."""
    a = math.radians(angle)
    ux, uy = math.cos(a), math.sin(a)                 # along the leaf
    nx, ny = -uy, ux                                  # across it

    def at(t: float, side: float) -> tuple:
        curl = -0.06 * length * t * t                 # the tip lifts
        return ((bx + ux * length * t + nx * side) * scale,
                (by + uy * length * t + ny * side + curl) * scale)

    steps = [i / 40 for i in range(41)]
    upper = [at(t, _blade(t, half)) for t in steps]
    lower = [at(t, -_blade(t, half)) for t in steps]
    whole = upper + lower[::-1]
    middle = [at(t, 0) for t in steps]
    draw.polygon(whole, fill=LEAF + (255,))
    draw.polygon((lower if ny < 0 else upper) + middle[::-1], fill=LEAF_DARK + (255,))

    draw.line(middle[:-3], fill=LEAF_VEIN + (255,), width=max(1, int(scale * 0.9)),
              joint="curve")
    for i, t in enumerate((0.25, 0.42, 0.58, 0.74)):
        for side in ((1, -1) if i % 2 == 0 else (-1, 1)):
            tip = min(0.95, t + 0.14)
            draw.line([at(t, 0), at(tip, side * _blade(tip, half) * 0.75)],
                      fill=LEAF_VEIN + (230,), width=max(1, int(scale * 0.6)))


def _leaves(scale: int) -> Any:
    import numpy as np
    from PIL import Image, ImageDraw
    n = 128 * scale
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ys, xs = np.mgrid[0:n, 0:n].astype(float) / scale
    for one in LEAVES:
        # Light each leaf's curved surface from the same direction as the melon.
        layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        _leaf(ImageDraw.Draw(layer), *one, scale)
        bx, by, angle, length, half = one
        a = math.radians(angle)
        cx, cy = bx + math.cos(a) * length * .50, by + math.sin(a) * length * .50
        rx = max(half, abs(math.cos(a)) * length * .52)
        ry = max(half * .8, abs(math.sin(a)) * length * .52)
        shade, highlight = _light_masks((xs - cx) / rx, (ys - cy) / ry)
        pixels = np.array(layer)
        colour = pixels[..., :3] * (1 - shade[..., None]) + np.array((116, 164, 194)) * shade[..., None]
        pixels[..., :3] = np.clip(colour * (1 - highlight[..., None]) +
                                 np.array((255, 245, 102)) * highlight[..., None], 0, 255).astype(np.uint8)
        image = Image.alpha_composite(image, Image.fromarray(pixels, "RGBA"))
    return _brush_texture(image, scale)


def eye_geometry(rx: float, ry: float, iris: float, look: Tuple[float, float]) -> tuple:
    """An inset ellipse and gaze offset whose sum stays inside the white."""
    ix, iy = min(iris, rx * .84), min(iris, ry * .84)
    ratio = max(ix / rx, iy / ry)
    lx, ly = look
    norm = max(1.0, math.hypot(lx, ly))
    travel = (1 - ratio) * .75
    return lx / norm * rx * travel, ly / norm * ry * travel, ix, iy


# -- the face, every frame ----------------------------------------------------
def draw_face(image: Any, scale: int, look: Tuple[float, float] = (0.0, 0.0),
              blink: float = 0.0, mood: str = "idle", t: float = 0.0,
              smile: Any = None) -> None:
    """The eyes - looking `look` (each of x, y from -1 to 1), `blink` shut -
    the brows and the mouth, for `mood`, onto the body.

    `smile`, 0 to 1: HOW FAR INTO THE HOVER'S SMILE (the user, 2026-11-03:
    the smile came at once and it felt like a jolt) - the eyes close as in a
    blink and open again as ^ ^, the brows rise, the mouth widens. None: all
    of it for "hover", none for the rest.

    "grabbed", while it is carried or caught sneaking (the user, 2026-11-04:
    "被抓包的表情，不要一直微笑"), as the user's earlier companion looked when it was
    yanked: softly widened sparkling eyes, a tiny pink surprised mouth,
    and pastel pink cheeks. "sneaky", going into the side of the screen:
    half-shut eyes sliding round."""
    from PIL import Image, ImageDraw
    target = image
    image = Image.new("RGBA", target.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    S = scale

    def oval(cx, cy, rx, ry, **kw):
        draw.ellipse(((cx - rx) * S, (cy - ry) * S, (cx + rx) * S, (cy + ry) * S), **kw)

    def eye_white(cx, cy, rx, ry):
        import numpy as np
        w, h = max(2, int(2 * rx * S) + 1), max(2, int(2 * ry * S) + 1)
        yy, xx = np.mgrid[0:h, 0:w].astype(float)
        wash = np.clip((.45 * xx / max(1, w - 1) + .65 * yy / max(1, h - 1) - .65) / .45, 0, 1) * .45
        rgb = 255 * (1 - wash[..., None]) + np.array((187, 218, 244)) * wash[..., None]
        patch = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, w - 1, h - 1), fill=255)
        image.paste(patch, (int((cx - rx) * S), int((cy - ry) * S)), mask)

    def eye_sparkles(px, py, ix, iy):
        # Three aligned pupil glints, with a separate arc on the iris ring.
        for dx, rw, rh in ((-.39, .13, .10), (0, .19, .16), (.39, .13, .10)):
            oval(px + ix * dx, py + iy * .10, ix * rw,
                 iy * rh, fill=CREAM + (255,))
        oval(px + ix * .56, py - iy * .56, ix * .18,
             iy * .18, fill=CREAM + (255,))

    width = lambda w: max(1, int(round(S * w)))      # noqa: E731
    lx, ly = max(-1.0, min(1.0, look[0])), max(-1.0, min(1.0, look[1]))
    grabbed, sneaky = mood == "grabbed", mood == "sneaky"
    if grabbed:
        blink = 0.0
    if sneaky:
        blink = max(blink, 0.4)
    level = (1.0 if mood == "hover" else 0.0) if smile is None else max(0.0, min(1.0, float(smile)))
    happy = level >= 0.5
    # On the way into the smile (and out of it), the eyes close as in a blink.
    blink = max(blink, 1.0 - abs(2.0 * level - 1.0)) if 0.0 < level < 1.0 else blink
    for i, (cx, cy, rx, ry, ir) in enumerate(EYES):
        lift = (-2.2 if mood == "notification" else 1.6 if sneaky
                else -2.2 * level)
        tilt = 2.0 if mood == "thinking" and i == 0 else 0.0
        bx = cx + 1
        if grabbed:                                 # soft surprised, sparkling eyes
            wide = rx * 1.12
            oval(cx, cy, wide + 1.0, ry + 1.8, fill=INK + (255,))
            eye_white(cx, cy, wide, ry + .8)
            dx, dy, ix, iy = eye_geometry(wide, ry + .8, ir * 1.05, (lx, ly))
            px, py = cx + dx, cy + dy
            oval(px, py, ix, iy, fill=IRIS + (255,))
            oval(px, py, ix * .76, iy * .76, fill=PUPIL + (255,))
            eye_sparkles(px, py, ix, iy)
            draw.arc(((cx - rx * .75) * S, (cy - ry - 5) * S,
                      (cx + rx * .75) * S, (cy - ry + 1) * S),
                     start=210, end=330, fill=BROW + (255,), width=width(1.3))
            continue
        # The pair rises toward the middle, with rounded painted ends.
        slope = 1 if cx > MOUTH[0] else -1
        by = cy - ry - 3 + lift - tilt
        points = ((bx - rx * .8, by - slope * 1.8),
                  (bx + rx * .8, by + slope * 1.8))
        draw.line([(x * S, y * S) for x, y in points],
                  fill=BROW + (255,), width=width(2.6))
        for x, y in points:
            oval(x, y, 1.3, 1.3, fill=BROW + (255,))
        if happy:                                   # ^ ^
            draw.arc(((cx - rx * 0.8) * S, (cy - ry * 0.5) * S, (cx + rx * 0.8) * S,
                      (cy + ry * 0.7) * S), start=200, end=340, fill=IRIS + (255,),
                     width=width(1.8))
            continue
        open_ = max(0.0, 1.0 - blink)
        if open_ < 0.15:                            # shut: a line
            draw.arc(((cx - rx) * S, (cy - ry * 0.4) * S, (cx + rx) * S, (cy + ry * 0.4) * S),
                     start=10, end=170, fill=IRIS + (255,), width=width(1.6))
            continue
        ery = ry * open_
        oval(cx, cy, rx + 1.1, ery + 1.1, fill=INK + (255,))
        eye_white(cx, cy, rx, ery)
        dx, dy, ix, iy = eye_geometry(rx, ery, ir, (lx, ly))
        px, py = cx + dx, cy + dy
        oval(px, py, ix, iy, fill=IRIS + (255,))
        oval(px, py, ix * .76, iy * .76, fill=PUPIL + (255,))
        eye_sparkles(px, py, ix, iy)
        if sneaky:                                  # the lid, straight across
            draw.line(((cx - rx - 1.2) * S, (cy - ery) * S, (cx + rx + 1.2) * S, (cy - ery) * S),
                      fill=INK + (255,), width=width(2.0))
    mx, my = MOUTH
    if mood == "talking":
        h = 3.5 + 2.5 * abs(math.sin(t * 9))
        oval(mx, my + 1, 4.2, h, fill=(188, 137, 152, 255), outline=INK + (255,), width=width(1.1))
        oval(mx, my + 1 + h * 0.5, 2.6, h * 0.35, fill=(230, 179, 191, 255))
    elif grabbed:                                   # a small round o
        oval(mx, my + 2, 2.7, 3.0, fill=INK + (255,))
        oval(mx, my + 2.5, 1.5, 1.7, fill=(230, 179, 191, 255))
    elif sneaky:                                    # pressed together, to one side
        draw.line(((mx - 3.2) * S, (my + 1.4) * S, (mx + 3.2) * S, (my - 0.2) * S),
                  fill=INK + (255,), width=width(1.5))
    elif mood == "thinking":
        draw.line(((mx - 3) * S, (my + 1) * S, (mx + 3) * S, (my - 0.5) * S),
                  fill=INK + (255,), width=width(1.4))
    else:
        w = 5.0 + 1.5 * level
        draw.arc(((mx - w) * S, (my - 4) * S, (mx + w) * S, (my + 3.5) * S), start=20, end=160,
                 fill=INK + (255,), width=width(1.5))
    # Pink cheeks for a notification only: on a smile they read as dimples.
    if mood == "notification":
        for cx, cy in CHEEKS:
            oval(cx, cy, 3.6, 2.0, fill=BLUSH + (110,))
    if grabbed:                                     # cheeks, solid: Pillow does not blend
        for cx, cy in CHEEKS:
            oval(cx + 1, cy + 3, 4.2, 3.0, fill=PINK + (255,))
    # Texture only the face layer, so the body grain is not applied twice.
    face = _brush_texture(image, scale, strength=.65)
    factor = RIG["face_scale"]
    ox, oy = (one * scale for one in RIG["face_origin"])
    face = face.transform(face.size, Image.Transform.AFFINE,
                          (1 / factor, 0, ox * (1 - 1 / factor),
                           0, 1 / factor, oy * (1 - 1 / factor)),
                          resample=Image.Resampling.BICUBIC)
    target.alpha_composite(face)



def _extras(image: Any, scale: int, mood: str, t: float) -> None:
    """The "!" of a notification, and the thought bubbles."""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    S = scale
    if mood == "notification":
        x, y, r = BADGE
        draw.ellipse(((x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S),
                     fill=(226, 64, 64, 255), outline=(255, 255, 255, 255),
                     width=max(1, int(S * 1.6)))
        draw.line((x * S, (y - 5.5) * S, x * S, (y + 2.5) * S), fill=(255, 255, 255, 255),
                  width=max(1, int(S * 2.4)))
        draw.ellipse(((x - 1.4) * S, (y + 4.2) * S, (x + 1.4) * S, (y + 7) * S),
                     fill=(255, 255, 255, 255))
    elif mood == "thinking":
        shown = int(t * 2.5) % 4                    # one more each beat
        for i, (x, y, r) in enumerate(BUBBLES):
            if i < shown:
                draw.ellipse(((x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S),
                             fill=(255, 255, 255, 235), outline=OUTLINE + (255,),
                             width=max(1, int(S * 0.8)))


class Watermelon:
    """The character, painted once at `scale` times and framed on demand."""

    #: It can go into the side of the screen (ui/avatar_tuck.py).
    TUCKS = True

    def __init__(self, scale: int = 3, package: Any = None) -> None:
        """Painted afresh, or - from a package's layers - read, which is
        quicker at start."""
        self.scale = scale
        self._leaf_boxes: dict = {}
        self._quick: Any = None
        layers = Path(package) / "layers" if package else None
        if layers is not None and (layers / "body.png").exists() \
                and (layers / "leaves.png").exists():
            from PIL import Image
            n = 128 * scale
            with Image.open(layers / "body.png") as body, \
                    Image.open(layers / "leaves.png") as leaves:
                self.body = body.convert("RGBA").resize((n, n), Image.LANCZOS)
                self.leaves = leaves.convert("RGBA").resize((n, n), Image.LANCZOS)
            return
        self.body = _body(scale)
        self.leaves = _leaves(scale)

    def frame(self, mood: str = "idle", look: Tuple[float, float] = (0.0, 0.0),
              blink: float = 0.0, t: float = 0.0, squash: float = 1.0,
              hop: float = 0.0, size: int = 128, facing: str = "left",
              smile: Any = None, tuck: float = 0.0, lean: float = 0.0,
              grip: float = 0.0, reach: float = 34.0, edge: str = "side",
              turn: float = 0.0, part: float = 0.0,
              flail: Tuple[float, float] = (0.0, 0.0)) -> Any:
        """One frame, `size` pixels square. `squash` below 1 flattens the
        body on the ground and above 1 stretches it; `hop` lifts it.
        `facing`: "left", as it is drawn, or "right" - the whole of it turned
        round, its eyes still on the pointer. `tuck`, 0 to 1: how far the body
        has gone off the leaves, away from the way it faces. `lean`: degrees
        the body tips over on the screen, anticlockwise - pulled along, it
        leans back; the leaves hang as they are. `grip`, 0 to 1: how far the
        leaves have turned to stand up the side of the screen and hold on to
        it (the user, 2026-11-04: "竖着的叶子群"), `reach` of the 128 units of
        the window from that side. `edge`: "side" - to the left of the way it
        is drawn, the right, turned for the left - "top" or "bottom". `turn`,
        0 to 1: how far it has turned, coming out of a side head first (the
        user, 2026-11-04): out of the left or right, the whole of it on end,
        taller than wide, its eyes one above the other; out of the top, upside
        down, its mouth above its eyes. Out of the bottom it is the way up it
        is drawn, and does not turn."""
        from PIL import Image, ImageOps
        if facing == "right":
            turned = self.frame(mood, (-look[0], look[1]), blink, t, squash, hop, size, "left",
                                smile, tuck, -lean, grip, reach, edge, turn, part, flail)
            return ImageOps.mirror(turned)
        body = self.body.copy()
        if turn:
            # THE WHOLE OF IT TURNED, its head to the room: on end out of a
            # side, taller than it is wide; upside down out of the top. The
            # face turns with it but is put where the room can see it: turned
            # with the body it would go round to the far side. Its eyes stay
            # on the pointer, whatever is up on the screen for it now.
            hanging = edge == "top"
            sees = (-look[0], -look[1]) if hanging else (-look[1], look[0])
            face = Image.new("RGBA", body.size, (0, 0, 0, 0))
            draw_face(face, self.scale, sees if turn > 0.5 else look, blink, mood, t, smile)
            left, top, right, bottom = face.getbbox() or (0, 0, *body.size)
            middle = ((left + right) / 2, (top + bottom) / 2)
            angle = (180 if hanging else 90) * turn
            face = face.rotate(angle, resample=Image.BILINEAR, center=middle)
            wanted = ((middle[0], HANGING_FACE_Y * self.scale) if hanging
                      else (TURNED_FACE_X * self.scale, body.size[1] / 2))
            placed = Image.new("RGBA", body.size, (0, 0, 0, 0))
            placed.paste(face, (int(turn * (wanted[0] - middle[0])),
                                int(turn * (wanted[1] - middle[1]))), face)
            body = body.rotate(angle, resample=Image.BILINEAR)
            body.alpha_composite(placed)
        else:
            draw_face(body, self.scale, look, blink, mood, t, smile)
        n = body.size[0]
        if abs(squash - 1.0) > 1e-3 or hop:
            w = max(1, int(n * (2 - squash) ** 0.5))
            h = max(1, int(n * squash))
            body = body.resize((w, h), Image.BILINEAR)
            layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
            ground = GROUND * self.scale
            layer.paste(body, ((n - w) // 2, int(ground - ground * squash - hop * self.scale)),
                        body)
            body = layer
        if lean:
            body = body.rotate(lean, resample=Image.BILINEAR,
                               center=(n / 2, GROUND * self.scale))
        if tuck:
            layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
            shift = int(tuck * TUCK_SHIFT * self.scale)
            layer.paste(body, {"top": (0, -shift), "bottom": (0, shift)}.get(edge, (shift, 0)), body)
            body = layer
        leaves = self.leaves_moved(part, flail) if part or any(flail) else self.leaves
        leaves = self.leaves_at(grip, reach, edge, leaves) if grip else leaves
        # The leaf bed overlaps the melon in every pose.
        out = Image.alpha_composite(body, leaves)
        _extras(out, self.scale, mood, t)
        return out.resize((size, size), Image.LANCZOS) if size != n else out


    def leaves_moved(self, part: float = 0.0, flail: Tuple[float, float] = (0.0, 0.0)) -> Any:
        """The two halves of the bed moved on their stems: `part`, 0 to 1 and a little past it as
        they sway, apart for its head; `flail`, degrees, each half's own, held and struggling.
        No leaf crosses the middle, so the picture is cut there - a package's too."""
        from PIL import Image
        n, scale = self.leaves.size[0], self.scale
        out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        halves = (((0, 0, n // 2, n), LEFT_STEMS, 1, flail[0]),
                  ((n // 2, 0, n, n), RIGHT_STEMS, -1, flail[1]))
        for box, stems, way, more in halves:
            half = Image.new("RGBA", (n, n), (0, 0, 0, 0))
            half.paste(self.leaves.crop(box), box[:2])
            half = half.rotate(way * part * PART_SWING + more, resample=Image.BILINEAR,
                               center=(stems[0] * scale, stems[1] * scale))
            moved = Image.new("RGBA", (n, n), (0, 0, 0, 0))
            moved.paste(half, (-way * int(part * PART_APART * scale), 0))
            out = Image.alpha_composite(out, moved)
        return out

    def leaves_at(self, grip: float, reach: float, edge: str = "side", leaves: Any = None) -> Any:
        """The leaves turned from their bed to lie along a side of the screen,
        `reach` units in - their stems past it, as if holding on. At the top
        they are as they grow, stems up; at the bottom, turned over."""
        from PIL import Image
        n, scale = self.leaves.size[0], self.scale
        turn = {"side": -90, "top": 0, "bottom": 180}[edge]
        middle = (n / 2, n / 2)
        turned = (self.leaves if leaves is None else leaves).rotate(
            turn * grip, resample=Image.BILINEAR, center=middle)
        box = self._leaf_boxes.get(edge)
        if box is None:                  # the same for every frame at that edge
            box = self._leaf_boxes[edge] = self.leaves.rotate(
                turn, resample=Image.BILINEAR, center=middle).getchannel("A").getbbox()
        if edge == "top":
            at = (0, int(grip * (128 - reach - GRIP_PAST - box[1] / scale) * scale))
        elif edge == "bottom":
            at = (0, int(grip * (reach + GRIP_PAST - box[3] / scale) * scale))
        else:
            at = (int(grip * (reach + GRIP_PAST - box[2] / scale) * scale), 0)
        layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        layer.paste(turned, at, turned)
        return layer

    def quick(self) -> "Watermelon":
        """THE SAME CHARACTER ON A SMALLER CANVAS, for frames that are gone at once: a jump
        (the user, 2026-11-04: "跳出来的动画特别慢"). A turned frame costs several times a
        still one, and at that cost a jump was twenty frames a second. Its edges are
        coarser, which does not show in the air."""
        if self.scale == 1:
            return self
        if self._quick is None:
            from PIL import Image
            twin = copy.copy(self)
            twin.scale, twin._leaf_boxes, twin._quick = 1, {}, None
            twin.body = self.body.resize((128, 128), Image.LANCZOS)
            twin.leaves = self.leaves.resize((128, 128), Image.LANCZOS)
            self._quick = twin
        return self._quick

    def icon(self) -> Any:
        """The app logo includes the face, vine and foreground leaves."""
        from PIL import Image, ImageOps
        body = self.body.copy()
        draw_face(body, self.scale, (0.0, 0.0), 0.0, "idle", 0.0)
        body = Image.alpha_composite(body, self.leaves)
        box = body.getbbox()
        if box:
            body = body.crop(box)
        side = max(body.size)
        return ImageOps.pad(body, (side, side), method=Image.LANCZOS, color=(0, 0, 0, 0))


# -- the package ----------------------------------------------------------------
def facing_for(left: int, width: int, screen: int, was: str = "left",
               margin: int = 40) -> str:
    """WHICH WAY IT FACES on the screen: toward the middle - on the right
    half, to the left; on the left half, to the right. Near the middle it
    keeps the way it faced, so dragging it across does not flicker."""
    middle = left + width / 2
    if middle > screen / 2 + margin:
        return "left"
    if middle < screen / 2 - margin:
        return "right"
    return was


def write_package(target: Path) -> Path:
    """The character package: its manifest (with the rig), a still for each
    state - for anything that shows a picture - and the body and leaves the
    settings page draws the face over."""
    target = Path(target)
    art = Watermelon(scale=8)
    stills = {"idle": [dict(), dict(blink=1.0)],
              "talking": [dict(mood="talking", t=0.0), dict(mood="talking", t=0.17)],
              "thinking": [dict(mood="thinking", look=(-0.7, -0.9), t=1.3)],
              "notification": [dict(mood="notification", hop=3.0), dict(mood="notification")]}
    for state, frames in stills.items():
        folder = target / state
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.png"):
            old.unlink()
        for i, said in enumerate(frames):
            art.frame(**said).save(folder / f"frame_{i:02d}.png")
    layers = target / "layers"
    layers.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    art.body.resize((512, 512), Image.LANCZOS).save(layers / "body.png")
    art.leaves.resize((512, 512), Image.LANCZOS).save(layers / "leaves.png")
    manifest = {"name": NAME, "display_name": DISPLAY_NAME, "label": LABEL, "version": "2.0.0",
                "author": "Personal Agent WaterMelon", "states": list(STATES), "size": [128, 128],
                "rig": RIG}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


def layers_of(package_path: Any) -> Dict[str, str]:
    """The body and the leaves, as the page shows them (data URLs)."""
    out = {}
    for part in ("body", "leaves"):
        path = Path(package_path) / "layers" / f"{part}.png"
        if path.exists():
            out[part] = "data:image/png;base64," + base64.b64encode(
                path.read_bytes()).decode("ascii")
    return out


__all__ = ["NAME", "DISPLAY_NAME", "LABEL", "RIG", "Watermelon", "draw_face", "write_package",
           "facing_for",
           "layers_of"]

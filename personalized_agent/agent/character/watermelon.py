"""Watermelon: the first avatar - a watermelon on a bed of leaves, its face
to the left, whose eyes follow the pointer.

Drawn here, from shapes, rather than kept as pictures: the body and the
leaves are painted once (a melon with wavy two-green stripes that follow its
curve, a gloss on the upper left, lettuce leaves under it), and the face is
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

NAME = "watermelon"
DISPLAY_NAME = "Watermelon"
STATES = ("idle", "talking", "thinking", "notification")

OUTLINE = (28, 70, 22)
LEAF = (128, 194, 70)
LEAF_DARK = (88, 154, 48)
LEAF_VEIN = (186, 228, 128)
BASE = (92, 162, 56)
STRIPE = (40, 110, 36)
CREAM = (247, 238, 216)
IRIS = (52, 32, 20)
PUPIL = (22, 12, 8)
INK = (26, 40, 18)
BROW = (30, 86, 26)
BLUSH = (240, 120, 120)
PINK = (255, 157, 176)
TEAR = (176, 226, 250)

#: The body, in a 128-unit box: centre, half length and half height, its
#: tilt (the left end a little higher - it faces left) and how square its
#: ends are.
CX, CY, A, B, TILT, P = 66.0, 62.0, 55.0, 35.0, math.radians(9), 2.35
#: The leaves under it, a bed of them: where each grows from, its direction
#: (degrees, 0 to the right, 90 down), its length and its half width.
LEAVES = ((48, 95, 176, 40, 12.5), (80, 95, 4, 40, 12.5), (52, 99, 160, 30, 10.5),
          (76, 99, 20, 30, 10.5))
#: The face, to the left: each eye's centre, half width, half height and iris
#: radius - the near one larger - and the mouth.
EYES = ((54.0, 59.0, 8.4, 10.4, 6.6), (33.0, 56.0, 6.6, 9.2, 5.4))
MOUTH = (42.0, 73.0)
#: The cheeks, the "!" of a notification and the thought bubbles - by the face.
CHEEKS = ((62, 70), (26, 66))
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

#: What the page needs to draw the same face (api: GET /api/characters).
RIG = {"kind": NAME, "box": 128, "eyes": [list(one) for one in EYES], "mouth": list(MOUTH),
       "ground": GROUND, "cheeks": [list(one) for one in CHEEKS], "badge": list(BADGE),
       "bubbles": [list(one) for one in BUBBLES], "brow_arc": [220, 340], "brow_shift": 1,
       "looks_from": [44, 57], "faces": "left",
       "colours": {"outline": "#%02x%02x%02x" % OUTLINE, "cream": "#%02x%02x%02x" % CREAM,
                   "iris": "#%02x%02x%02x" % IRIS, "pupil": "#%02x%02x%02x" % PUPIL,
                   "ink": "#%02x%02x%02x" % INK, "brow": "#%02x%02x%02x" % BROW,
                   "blush": "#%02x%02x%02x" % BLUSH}}


# -- the body and the leaves, painted once ------------------------------------
def _body(scale: int) -> Any:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    n = 128 * scale
    ys, xs = np.mgrid[0:n, 0:n].astype(np.float32) / scale
    dx, dy = xs - CX, ys - CY
    c, s = math.cos(-TILT), math.sin(-TILT)
    u = (dx * c - dy * s) / A
    v = (dx * s + dy * c) / B
    v = v / (1.0 + 0.06 * u)                         # fuller at the back end
    r = np.abs(u) ** P + np.abs(v) ** P
    col = np.empty((n, n, 3), np.float32)
    col[:] = BASE
    # STRIPES: wide soft bands by latitude, so they follow the curve and
    # gather at the ends, with blobby edges.
    across = np.clip(1.0 - np.abs(u) ** P, 1e-4, 1.0) ** (1.0 / P)
    lat = v / across
    band = np.zeros((n, n), bool)
    for k, c0 in enumerate((-0.80, -0.40, 0.0, 0.40, 0.80)):
        wob = (0.06 * np.sin(u * 4.2 + k * 1.9) + 0.035 * np.sin(u * 11 + k * 2.7)
               + 0.014 * np.sin(u * 29 + k * 4.1))
        width = 0.12 + 0.03 * np.sin(u * 7.5 + k)
        band |= np.abs(lat - c0 - wob) < width
    col[band] = STRIPE
    fine = np.abs(lat * 5 % 1 - 0.5) < 0.04
    col[fine & ~band] = np.array(BASE, np.float32) * 0.93
    # Lit from the upper left: darker toward the bottom and the right end,
    # and toward the rim.
    shade = 1.0 - 0.28 * np.clip((v + 0.2) / 1.2, 0, 1) - 0.08 * np.clip(-u, 0, 1)
    lift = 0.10 * np.clip(-(v + 0.3), 0, 1)
    col = col * shade[..., None] + 255 * lift[..., None]
    col = col * (1 - 0.18 * np.clip((r - 0.80) / 0.2, 0, 1))[..., None]
    out = np.zeros((n, n, 4), np.float32)
    out[..., :3] = col
    out[..., 3] = (r <= 1.0) * 255
    ring = (r > 1.0) & (r <= 1.085)
    out[ring, :3] = OUTLINE
    out[ring, 3] = 255
    image = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")
    # The gloss.
    gloss = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(gloss)
    for cx, cy, rx, ry, alpha in ((94, 40, 11, 4.6, 120), (104, 50, 3.2, 2.2, 110)):
        draw.ellipse(((cx - rx) * scale, (cy - ry) * scale, (cx + rx) * scale,
                      (cy + ry) * scale), fill=(255, 255, 255, alpha))
    gloss = gloss.rotate(-14, center=(94 * scale, 40 * scale)).filter(
        ImageFilter.GaussianBlur(scale * 0.6))
    return Image.alpha_composite(image, gloss)


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
    draw.line(whole + [whole[0]], fill=OUTLINE + (255,), width=max(1, int(scale * 1.2)),
              joint="curve")
    draw.line(middle[:-3], fill=LEAF_VEIN + (255,), width=max(1, int(scale * 0.9)),
              joint="curve")
    for i, t in enumerate((0.25, 0.42, 0.58, 0.74)):
        for side in ((1, -1) if i % 2 == 0 else (-1, 1)):
            tip = min(0.95, t + 0.14)
            draw.line([at(t, 0), at(tip, side * _blade(tip, half) * 0.75)],
                      fill=LEAF_VEIN + (230,), width=max(1, int(scale * 0.6)))


def _leaves(scale: int) -> Any:
    from PIL import Image, ImageDraw
    n = 128 * scale
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for one in LEAVES:
        _leaf(draw, *one, scale)
    return image


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
    yanked: round white eyes staring, plain round pupils, a small round mouth,
    pink cheeks, two strokes of being pulled - and a drop of sweat. "sneaky", going into the side of the screen:
    half-shut eyes sliding round."""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    S = scale

    def oval(cx, cy, rx, ry, **kw):
        draw.ellipse(((cx - rx) * S, (cy - ry) * S, (cx + rx) * S, (cy + ry) * S), **kw)

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
        if grabbed:                                 # staring: round, white, no brows
            wide = rx * 1.25
            oval(cx, cy, wide + 1.1, wide * 1.17 + 1.1, fill=INK + (255,))
            oval(cx, cy, wide, wide * 1.17, fill=(255, 255, 255, 255))
            oval(cx - wide * 0.14, cy - 0.4, wide * 0.42, wide * 0.46, fill=PUPIL + (255,))
            continue
        draw.arc(((bx - rx * 0.95) * S, (cy - ry - 8 + lift - tilt) * S,
                  (bx + rx * 0.95) * S, (cy - ry + 2 + lift) * S),
                 start=220, end=340, fill=BROW + (255,), width=width(1.6))
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
        oval(cx, cy, rx, ery, fill=CREAM + (255,))
        px = cx + lx * (rx - ir * 0.75)
        py = cy + ly * max(0.0, ery - ir * 0.7)
        irr = min(ir, ery)
        oval(px, py, ir, irr, fill=IRIS + (255,))
        oval(px, py, ir * 0.55, irr * 0.55, fill=PUPIL + (255,))
        oval(px + ir * 0.35, py - irr * 0.45, ir * 0.32, ir * 0.32, fill=(255, 255, 255, 255))
        oval(px - ir * 0.35, py + irr * 0.35, ir * 0.14, ir * 0.14, fill=(255, 255, 255, 220))
        if sneaky:                                  # the lid, straight across
            draw.line(((cx - rx - 1.2) * S, (cy - ery) * S, (cx + rx + 1.2) * S, (cy - ery) * S),
                      fill=INK + (255,), width=width(2.0))
    mx, my = MOUTH
    if mood == "talking":
        h = 3.5 + 2.5 * abs(math.sin(t * 9))
        oval(mx, my + 1, 4.2, h, fill=(70, 24, 20, 255), outline=INK + (255,), width=width(1.1))
        oval(mx, my + 1 + h * 0.5, 2.6, h * 0.35, fill=(214, 92, 92, 255))
    elif grabbed:                                   # a small round o
        oval(mx, my + 3.5, 3.7, 3.4, fill=INK + (255,))
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
        for one in (((21, 50), (14, 44)), ((21, 74), (14, 80))):     # the strokes
            draw.line((one[0][0] * S, one[0][1] * S, one[1][0] * S, one[1][1] * S),
                      fill=LEAF_VEIN + (255,), width=width(1.6))
        x, y = SWEAT                                # and a drop of sweat
        tip = ((x + 1.5) * S, (y - 13) * S)
        draw.polygon((tip, ((x - 6.2) * S, (y - 1) * S), ((x + 6.2) * S, (y - 1) * S)),
                     fill=OUTLINE + (255,))
        oval(x, y + 1, 7.0, 7.4, fill=OUTLINE + (255,))
        draw.polygon((((x + 1.4) * S, (y - 11) * S), ((x - 5.2) * S, (y - 0.5) * S),
                      ((x + 5.2) * S, (y - 0.5) * S)), fill=TEAR + (255,))
        oval(x, y + 1, 6.0, 6.4, fill=TEAR + (255,))
        oval(x - 2.2, y + 0.2, 1.5, 2.2, fill=(255, 255, 255, 255))


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
              turn: float = 0.0) -> Any:
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
                                smile, tuck, -lean, grip, reach, edge, turn)
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
        leaves = self.leaves_at(grip, reach, edge) if grip else self.leaves
        # On end it is as tall as they are: they hold on in front of it.
        out = Image.alpha_composite(body, leaves) if turn > 0.5 else \
            Image.alpha_composite(leaves, body)
        _extras(out, self.scale, mood, t)
        return out.resize((size, size), Image.LANCZOS) if size != n else out


    def leaves_at(self, grip: float, reach: float, edge: str = "side") -> Any:
        """The leaves turned from their bed to lie along a side of the screen,
        `reach` units in - their stems past it, as if holding on. At the top
        they are as they grow, stems up; at the bottom, turned over."""
        from PIL import Image
        n, scale = self.leaves.size[0], self.scale
        turn = {"side": -90, "top": 0, "bottom": 180}[edge]
        middle = (n / 2, n / 2)
        turned = self.leaves.rotate(turn * grip, resample=Image.BILINEAR, center=middle)
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
        """THE ICON (the taskbar, the title bars, the tray): the melon and
        its face, without the leaves - at sixteen pixels the leaves only
        made the melon smaller (the user's choice) - cut to what is drawn
        and narrowed to as wide as it is tall, so it FILLS its square as
        other programs' icons do. (At 0.78 of its width it was still wider
        than tall, and filled 77% of the square's height: a size smaller.)"""
        from PIL import Image
        body = self.body.copy()
        draw_face(body, self.scale, (0.0, 0.0), 0.0, "idle", 0.0)
        box = body.getbbox()
        if box:
            body = body.crop(box)
        return body.resize((max(1, body.height), body.height), Image.LANCZOS)


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
    manifest = {"name": NAME, "display_name": DISPLAY_NAME, "version": "1.0.0",
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


__all__ = ["NAME", "DISPLAY_NAME", "RIG", "Watermelon", "draw_face", "write_package",
           "facing_for",
           "layers_of"]

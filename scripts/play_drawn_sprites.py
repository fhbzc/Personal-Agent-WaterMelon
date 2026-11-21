"""THE PLAY VIEW'S OWN DRAWN THINGS (made for the app; no outside source), drawn as the packs are drawn: shapes lit
from the top left in three or four tones, a dark outline round each - festival, fair and aquarium pieces no CC0 pack
had at 16 px (the user, 2026-11-17: "美工比较简单 ... 你做好点"). The fair's after RollerCoaster Tycoon's kinds of
thing - its rides, stalls and queues - drawn here, none of its pictures taken (they are not free to use).

    python scripts/play_drawn_sprites.py [sea.png]   ->  personalized_agent/api/web/play/drawn.png (specs printed)

Each thing is drawn on a canvas of its tiles (16 px a tile) and laid on the sheet as every other sheet is - 16 px a
tile, 1 px between. `sea.png` (the CC0 fish sheet) gives the fish tanks their fish.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web" / "play" / "drawn.png"
Colour = Tuple[int, int, int]
OUTLINE: Colour = (40, 28, 34)

# Ramps, darkest to lightest.
RED = [(122, 22, 34), (178, 34, 44), (222, 58, 58), (250, 120, 104)]
GOLD = [(140, 88, 20), (204, 142, 30), (244, 196, 64), (255, 238, 150)]
PINK = [(160, 48, 100), (216, 88, 150), (244, 140, 186), (255, 206, 226)]
BLUE = [(30, 70, 140), (50, 120, 200), (96, 170, 236), (180, 222, 255)]
SEA = [(24, 70, 120), (40, 110, 170), (70, 150, 205), (140, 200, 235)]
WOOD = [(84, 50, 30), (128, 80, 44), (170, 112, 62), (206, 150, 92)]
STEEL = [(70, 76, 92), (112, 120, 138), (160, 168, 184), (214, 220, 230)]
WHITE = [(160, 160, 176), (204, 204, 214), (236, 236, 240), (255, 255, 255)]
GREEN = [(30, 84, 52), (48, 130, 70), (84, 176, 92), (150, 214, 130)]
CREAM = [(180, 150, 110), (220, 192, 150), (244, 226, 190), (255, 246, 222)]
ORANGE = [(150, 60, 20), (214, 104, 30), (246, 150, 52), (255, 204, 120)]
PURPLE = [(70, 40, 110), (110, 66, 170), (150, 104, 214), (200, 170, 240)]
TEAL = [(20, 96, 100), (34, 146, 146), (70, 196, 186), (150, 236, 220)]
DARK = [OUTLINE, (60, 40, 46), (80, 56, 60), (100, 70, 72)]


class Canvas:
    """A small picture drawn in shapes; `done()` puts the outline round it."""

    def __init__(self, w_tiles: int, h_tiles: int) -> None:
        self.w, self.h = w_tiles * 16, h_tiles * 16
        self.px: Dict[Tuple[int, int], Colour] = {}

    def dot(self, x: float, y: float, c: Colour) -> None:
        x, y = int(round(x)), int(round(y))
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[(x, y)] = c

    @staticmethod
    def shade(ramp: Sequence[Colour], light: float) -> Colour:
        n = len(ramp)
        return ramp[max(0, min(n - 1, int(light * n)))]

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, ramp: Sequence[Colour], ribs: int = 0) -> None:
        """A lit ball or egg; `ribs` darker lines down it (a lantern's)."""
        for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
                dx, dy = (x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry
                d = dx * dx + dy * dy
                if d > 1:
                    continue
                nz = math.sqrt(max(0.0, 1 - d))
                light = 0.5 * nz + 0.5 * (0.5 - dx * 0.35 - dy * 0.45)
                tone = self.shade(ramp, light)
                if ribs and abs(math.sin((dx + 1) * math.pi * ribs / 2)) < 0.2 and abs(dx) < 0.85:
                    tone = ramp[max(0, list(ramp).index(tone) - 1)]
                self.dot(x, y, tone)
        self.dot(cx - rx * 0.4, cy - ry * 0.45, ramp[-1])           # its shine

    def rect(self, x0: float, y0: float, x1: float, y1: float, ramp: Sequence[Colour]) -> None:
        """A lit box: its top and left light, its bottom and right dark."""
        for y in range(int(y0), int(y1)):
            for x in range(int(x0), int(x1)):
                tone = ramp[2]
                if y == int(y0) or x == int(x0):
                    tone = ramp[3]
                elif y == int(y1) - 1 or x == int(x1) - 1:
                    tone = ramp[1]
                self.dot(x, y, tone)

    def fill(self, x0, y0, x1, y1, c: Colour) -> None:
        for y in range(int(y0), int(y1)):
            for x in range(int(x0), int(x1)):
                self.dot(x, y, c)

    def line(self, x0: float, y0: float, x1: float, y1: float, c: Colour, width: int = 1) -> None:
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
        steep = abs(y1 - y0) > abs(x1 - x0)
        for i in range(steps + 1):
            t = i / steps
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            for w in range(width):
                self.dot(x + (w if steep else 0), y + (0 if steep else w), c)

    def poly(self, points: List[Tuple[float, float]], ramp: Sequence[Colour]) -> None:
        """A polygon, lighter towards its top and left."""
        ys = [p[1] for p in points]
        top, bottom = min(ys), max(ys)
        for y in range(int(top), int(bottom) + 1):
            cuts = []
            for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
                if (ay <= y + 0.5 < by) or (by <= y + 0.5 < ay):
                    cuts.append(ax + (y + 0.5 - ay) / (by - ay) * (bx - ax))
            cuts.sort()
            for a, b in zip(cuts[::2], cuts[1::2]):
                for x in range(int(round(a)), int(round(b))):
                    light = 0.9 - 0.55 * (y - top) / max(1, bottom - top) - 0.2 * (x - a) / max(1, b - a)
                    self.dot(x, y, self.shade(ramp, light))

    def done(self, outline: bool = True) -> Image.Image:
        image = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        for (x, y), c in self.px.items():
            image.putpixel((x, y), (*c, 255))
        if outline:
            for (x, y) in list(self.px):
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.w and 0 <= ny < self.h and (nx, ny) not in self.px:
                        image.putpixel((nx, ny), (*OUTLINE, 255))
        return image


def striped_cone(c: Canvas, cx: float, top: float, bottom: float, half_at_bottom: float, a: Sequence[Colour],
                 b: Sequence[Colour], stripes: int = 10) -> None:
    """A tent's or a carousel's roof: stripes that bend round it, lit from the left."""
    for y in range(int(top), int(bottom)):
        half = 1.5 + (y - top) * half_at_bottom / (bottom - top)
        for x in range(int(cx - half), int(cx + half) + 1):
            u = max(-1.0, min(1.0, (x + 0.5 - cx) / half))
            ramp = a if int((math.asin(u) / math.pi + 0.5) * stripes) % 2 else b
            c.dot(x, y, c.shade(ramp, 0.8 - 0.35 * u - 0.2 * (y - top) / (bottom - top)))


# -- the New Year ----------------------------------------------------------------------------------------------------
def lantern() -> Image.Image:
    c = Canvas(1, 1)
    c.line(8, 0, 8, 2, GOLD[1])
    c.rect(5, 2, 11, 4, GOLD)
    c.ellipse(8, 8, 6.2, 4.6, RED, ribs=3)
    c.rect(5, 12, 11, 13, GOLD)
    for x in (6, 8, 10):
        c.line(x, 13, x, 15, GOLD[2])
    return c.done()


def lantern_string() -> Image.Image:
    c = Canvas(2, 1)
    for x in range(32):
        c.dot(x, 1 + round(2 * math.sin(x / 32 * math.pi)), WOOD[0])
    for cx in (5, 16, 27):
        top = 2 + round(2 * math.sin(cx / 32 * math.pi))
        c.rect(cx - 2, top + 1, cx + 3, top + 2, GOLD)
        c.ellipse(cx + 0.5, top + 6, 4.2, 3.6, RED, ribs=2)
        c.line(cx, top + 10, cx, top + 12, GOLD[2])
    return c.done()


def firecrackers() -> Image.Image:
    c = Canvas(1, 1)
    c.line(8, 0, 8, 3, WOOD[1])
    for x, y in ((5, 3), (9, 4), (6, 8), (10, 9)):
        c.rect(x - 1, y, x + 2, y + 5, RED)
        c.fill(x - 1, y + 2, x + 2, y + 3, GOLD[2])
        c.dot(x, y - 1, GOLD[3])
    c.ellipse(8, 14, 2.4, 1.6, GOLD)
    return c.done()


def fu_sign() -> Image.Image:
    """A red diamond with a gold 福 - from a system font when there is one, else a gold knot."""
    c = Canvas(1, 1)
    c.poly([(8, 0), (16, 8), (8, 16), (0, 8)], RED)
    image = c.done()
    try:
        from PIL import ImageDraw, ImageFont
        font = None
        for name in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "PingFang.ttc", "NotoSansCJK-Bold.ttc"):
            try:
                font = ImageFont.truetype(name, 10)
                break
            except OSError:
                continue
        if font is None:
            raise OSError("no font")
        glyph = Image.new("L", (16, 16), 0)
        ImageDraw.Draw(glyph).text((8, 8), "福", fill=255, font=font, anchor="mm")
        for y in range(16):
            for x in range(16):
                if glyph.getpixel((x, y)) > 100 and image.getpixel((x, y))[3]:
                    image.putpixel((x, y), (*GOLD[2], 255))
    except Exception:
        for x, y in ((8, 5), (7, 6), (9, 6), (6, 7), (10, 7), (7, 8), (9, 8), (8, 9), (8, 10)):
            image.putpixel((x, y), (*GOLD[2], 255))
    return image


def red_envelope() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(3, 2, 13, 15, RED)
    c.poly([(3, 2), (13, 2), (8, 7)], [RED[0], RED[1], RED[1], RED[2]])
    c.ellipse(8, 7, 2, 2, GOLD)
    return c.done()


def couplet_door() -> Image.Image:
    c = Canvas(2, 2)
    c.rect(4, 6, 28, 32, WOOD)
    c.rect(9, 9, 23, 32, RED)
    c.line(16, 9, 16, 32, RED[0])
    c.dot(14, 21, GOLD[3])
    c.dot(18, 21, GOLD[3])
    for x0 in (4, 24):
        c.rect(x0 + 1, 8, x0 + 4, 31, RED)
        for y in range(10, 30, 3):
            c.dot(x0 + 2, y, GOLD[2])
    c.rect(2, 2, 30, 6, RED)
    for x in range(6, 27, 4):
        c.dot(x, 4, GOLD[2])
    return c.done()


def firework() -> Image.Image:
    c = Canvas(2, 2)
    colours = [GOLD, RED, PINK, BLUE, TEAL]
    for n in range(16):
        a = n / 16 * 2 * math.pi
        ramp = colours[n % len(colours)]
        for d in range(3, 14):
            if d % 3 == 0 and d > 6:
                continue
            c.dot(16 + math.cos(a) * d, 16 + math.sin(a) * d, ramp[3 if d < 7 else 2])
    c.ellipse(16, 16, 2.5, 2.5, GOLD)
    return c.done(outline=False)


def lion_head() -> Image.Image:
    c = Canvas(2, 2)
    c.ellipse(16, 17, 13, 11, RED)
    c.ellipse(16, 9, 10, 5, GOLD)
    c.ellipse(10, 15, 3.5, 3.5, WHITE)
    c.ellipse(22, 15, 3.5, 3.5, WHITE)
    c.fill(10, 15, 12, 17, OUTLINE)
    c.fill(22, 15, 24, 17, OUTLINE)
    c.ellipse(16, 22, 4, 2.5, PINK)
    c.rect(7, 25, 25, 28, WHITE)
    for x in range(8, 25, 2):
        c.dot(x, 29, GOLD[2])
        c.dot(x, 30, GOLD[1])
    c.ellipse(16, 3.5, 2.5, 2.5, GOLD)
    return c.done()


def plum_tree() -> Image.Image:
    c = Canvas(2, 2)
    c.line(16, 30, 16, 18, WOOD[0], 2)
    for x1, y1 in ((6, 8), (26, 7), (10, 4), (22, 3), (16, 2), (4, 14), (28, 15)):
        c.line(16, 20, x1, y1, WOOD[0], 1)
    rnd = random.Random(7)
    for _ in range(70):
        a, d = rnd.random() * math.pi, rnd.random() * 13
        c.dot(16 + math.cos(a) * d * 1.1, 14 - math.sin(a) * d * 0.9, PINK[rnd.choice((1, 2, 2, 3))])
    c.rect(11, 28, 21, 32, STEEL)
    return c.done()


# -- the fair ----------------------------------------------------------------------------------------------------------
def balloons() -> Image.Image:
    c = Canvas(1, 1)
    spots = ((5, 5, RED), (11, 4, BLUE), (8, 8, GOLD))
    for x, y, _ramp in spots:
        c.line(x, y + 4, 8, 15, WHITE[1])
    for x, y, ramp in spots:
        c.ellipse(x, y, 3.2, 3.8, ramp)
    return c.done()


def bunting() -> Image.Image:
    c = Canvas(2, 1)
    for x in range(32):
        c.dot(x, 2 + round(2.5 * math.sin(x / 32 * math.pi)), WOOD[0])
    for n, x in enumerate(range(2, 30, 5)):
        y = 3 + round(2.5 * math.sin(x / 32 * math.pi))
        c.poly([(x, y), (x + 4, y), (x + 2, y + 5)], (RED, GOLD, BLUE, GREEN, PINK, PURPLE)[n % 6])
    return c.done()


def ticket_booth() -> Image.Image:
    c = Canvas(2, 2)
    c.rect(4, 12, 28, 31, WOOD)
    for x in range(2, 30):
        for y in range(5, 11):
            c.dot(x, y, (RED if (x // 4) % 2 == 0 else WHITE)[2 if y < 9 else 1])
    c.rect(8, 14, 24, 21, BLUE)
    c.ellipse(16, 18, 2.5, 2.5, CREAM)
    c.rect(9, 23, 23, 27, CREAM)
    c.fill(11, 25, 21, 26, RED[2])
    c.rect(10, 1, 22, 5, GOLD)
    return c.done()


def food_stand(top: Sequence[Colour], goods: Callable[[Canvas], None]) -> Image.Image:
    c = Canvas(2, 2)
    c.rect(4, 16, 28, 30, WOOD)
    for x in range(2, 30):
        for y in range(6, 12):
            c.dot(x, y, (top if (x // 4) % 2 == 0 else WHITE)[2 if y < 10 else 1])
    c.line(5, 12, 5, 16, STEEL[1])
    c.line(26, 12, 26, 16, STEEL[1])
    c.rect(4, 14, 28, 17, CREAM)
    goods(c)
    c.fill(6, 28, 9, 31, OUTLINE)
    c.fill(23, 28, 26, 31, OUTLINE)
    return c.done()


def ice_cream(c: Canvas) -> None:
    for x, ramp in ((9, PINK), (16, CREAM), (23, GREEN)):
        c.poly([(x - 2, 4), (x + 2, 4), (x, 9)], GOLD)
        c.ellipse(x, 3, 2.6, 2.4, ramp)
    c.rect(8, 20, 24, 26, BLUE)


def hot_dogs(c: Canvas) -> None:
    for x in (9, 16, 23):
        c.ellipse(x, 3.5, 3.2, 1.6, GOLD)
        c.line(x - 2, 3, x + 2, 3, RED[1])
    c.rect(8, 20, 24, 26, RED)


def candy(c: Canvas) -> None:
    for x in (10, 22):
        c.line(x, 13, x, 7, CREAM[0])
        c.ellipse(x, 4, 3.5, 3, PINK)
    c.rect(8, 20, 24, 26, PINK)


def ferris_wheel() -> Image.Image:
    c = Canvas(5, 5)
    cx, cy, r = 40, 34, 30
    for side in (-1, 1):
        c.line(cx, cy, cx + side * 20, 76, STEEL[0], 2)
    for n in range(12):
        a = n / 12 * 2 * math.pi
        c.line(cx, cy, cx + r * math.cos(a), cy + r * math.sin(a), STEEL[1])
    for a10 in range(0, 3600, 3):
        a = a10 / 3600 * 2 * math.pi
        for rr, tone in ((r, STEEL[3]), (r - 1, STEEL[2]), (r - 6, STEEL[1])):
            c.dot(cx + rr * math.cos(a), cy + rr * math.sin(a), tone)
    c.ellipse(cx, cy, 3.5, 3.5, GOLD)
    for n in range(12):
        a = n / 12 * 2 * math.pi
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        c.rect(x - 2.5, y + 1, x + 3.5, y + 6, (RED, BLUE, GOLD, GREEN, PINK, PURPLE)[n % 6])
        c.dot(x + 0.5, y, STEEL[3])
    c.rect(cx - 24, 74, cx + 24, 80, CREAM)
    for x in range(cx - 22, cx + 22, 5):
        c.dot(x, 76, RED[2])
    return c.done()


def carousel() -> Image.Image:
    c = Canvas(4, 4)
    cx = 32
    striped_cone(c, cx, 10, 28, 28, RED, WHITE)
    for x in range(cx - 30, cx + 30):
        if (x // 3) % 2 == 0:
            c.dot(x, 28, GOLD[2])
    c.line(cx, 3, cx, 10, WOOD[0])
    c.poly([(cx, 3), (cx + 6, 5), (cx, 7)], GOLD)
    c.rect(cx - 30, 52, cx + 30, 58, GOLD)
    c.rect(cx - 28, 58, cx + 28, 62, WOOD)
    for n, x in enumerate(range(cx - 24, cx + 25, 8)):
        c.line(x, 29, x, 52, GOLD[2])
        hy = 38 + (4 if n % 2 else -2)
        body = WHITE if n % 2 else CREAM
        c.ellipse(x + 0.5, hy, 3.6, 2.4, body)
        c.line(x + 3, hy - 1, x + 4, hy - 5, body[2], 2)
        c.ellipse(x + 5, hy - 5, 1.6, 1.2, body)
        c.line(x - 2, hy + 2, x - 3, hy + 5, body[1])
        c.line(x + 2, hy + 2, x + 3, hy + 5, body[1])
        c.dot(x, hy - 2, (RED, BLUE, GOLD, PINK, GREEN)[n % 5][2])
    return c.done()


def drop_tower() -> Image.Image:
    c = Canvas(2, 5)
    c.rect(13, 4, 19, 76, STEEL)
    for y in range(8, 74, 6):
        c.line(13, y, 18, y + 4, STEEL[0])
    c.ellipse(16, 4, 4, 3, RED)
    c.rect(6, 40, 26, 46, RED)
    for x in range(8, 25, 4):
        c.ellipse(x, 47, 1.5, 1.5, CREAM)
    c.rect(4, 74, 28, 80, CREAM)
    return c.done()


def swing_ship() -> Image.Image:
    c = Canvas(4, 3)
    for side in (-1, 1):
        c.line(32, 6, 32 + side * 24, 44, STEEL[0], 2)
    c.ellipse(32, 6, 3, 3, GOLD)
    c.line(32, 6, 22, 30, STEEL[1])
    c.line(32, 6, 42, 30, STEEL[1])
    c.poly([(12, 28), (52, 28), (46, 38), (18, 38)], WOOD)
    c.poly([(10, 23), (16, 28), (11, 28)], WOOD)
    c.poly([(54, 23), (48, 28), (53, 28)], WOOD)
    for x in range(18, 47, 5):
        c.dot(x, 31, GOLD[3])
    c.rect(4, 44, 60, 48, CREAM)
    return c.done()


def circus_tent() -> Image.Image:
    c = Canvas(3, 3)
    striped_cone(c, 24, 6, 30, 20, RED, CREAM, stripes=8)
    for y in range(30, 44):
        for x in range(4, 44):
            c.dot(x, y, (RED if (x // 5) % 2 else CREAM)[2 if x < 30 else 1])
    c.rect(19, 33, 29, 44, DARK)
    c.line(24, 1, 24, 6, WOOD[0])
    c.poly([(24, 1), (30, 3), (24, 5)], PURPLE)
    return c.done()


def queue_rail() -> Image.Image:
    c = Canvas(1, 1)
    for x in (1, 13):
        c.rect(x, 5, x + 2, 15, STEEL)
        c.ellipse(x + 1, 4.5, 1.6, 1.6, GOLD)
    c.line(2, 7, 14, 7, RED[2])
    c.line(2, 8, 14, 8, RED[1])
    return c.done()


def coaster_station() -> Image.Image:
    """The coaster's station: a roofed platform its train stands at, a sign over it."""
    c = Canvas(4, 2)
    c.rect(2, 16, 62, 30, CREAM)
    for x in range(4, 61, 8):
        c.line(x, 8, x, 16, STEEL[1], 2)
    for x in range(0, 64):
        for y in range(4, 9):
            c.dot(x, y, (BLUE if (x // 6) % 2 else WHITE)[2 if y < 7 else 1])
    c.rect(20, 0, 44, 5, RED)
    for x in range(23, 42, 3):
        c.dot(x, 2, GOLD[3])
    return c.done()


def coaster_track(vertical: bool) -> Image.Image:
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    put = (lambda x, y, col: image.putpixel((y, x) if vertical else (x, y), (*col, 255)))
    for i in range(1, 16, 4):
        for k in range(3, 13):
            put(i, k, WOOD[1])
            put(i + 1, k, WOOD[0])
    for k, tone in ((4, STEEL[3]), (5, STEEL[1]), (11, STEEL[3]), (12, STEEL[1])):
        for i in range(16):
            put(i, k, tone)
    return image


def coaster_car() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(2, 4, 14, 12, RED)
    c.rect(4, 5, 12, 9, DARK)
    c.ellipse(6, 6, 1.5, 1.5, CREAM)
    c.ellipse(10, 6, 1.5, 1.5, CREAM)
    c.rect(2, 11, 14, 13, GOLD)
    return c.done()


# -- the sea ----------------------------------------------------------------------------------------------------------
def fish_tank(fishes: List[Image.Image], w: int = 2, h: int = 1) -> Image.Image:
    c = Canvas(w, h)
    W, H = w * 16, h * 16
    c.rect(1, 1, W - 1, 3, STEEL)
    for y in range(3, H - 3):
        for x in range(2, W - 2):
            c.dot(x, y, c.shade(SEA, 0.9 - 0.65 * (y - 3) / max(1, H - 6)))
    for x in range(3, W - 3, 5):
        c.line(x, H - 4, x + (1 if x % 2 else -1), H - 4 - min(8, H // 2), GREEN[2])
    c.fill(2, H - 4, W - 2, H - 2, GOLD[2])
    c.rect(1, H - 2, W - 1, H, STEEL)
    image = c.done()
    for n, fish in enumerate(fishes[: max(1, (W * H) // 120)]):
        f = fish.copy()
        f.thumbnail((9 if h == 1 else 14, 7 if h == 1 else 10), Image.NEAREST)
        image.alpha_composite(f, (4 + (n * 13) % max(1, W - 14), 4 + (n * 7) % max(1, H - 12)))
    return image


def jellyfish() -> Image.Image:
    c = Canvas(1, 1)
    c.ellipse(8, 5, 5.5, 4, PINK)
    for x in (4, 6, 8, 10, 12):
        for y in range(8, 15):
            if (y + x) % 3:
                c.dot(x + round(math.sin(y / 2 + x)), y, PINK[2 if y < 11 else 1])
    return c.done()


def coral() -> Image.Image:
    c = Canvas(1, 1)
    c.line(8, 15, 8, 9, ORANGE[1], 2)
    for x0, y0, x1, y1, ramp in ((8, 11, 4, 4, ORANGE), (8, 10, 12, 3, ORANGE), (5, 7, 2, 3, ORANGE),
                                 (11, 6, 14, 6, PINK), (4, 14, 2, 9, PINK), (12, 14, 14, 10, PURPLE)):
        c.line(x0, y0, x1, y1, ramp[2])
        c.dot(x1, y1, ramp[3])
    c.rect(4, 14, 13, 16, STEEL)
    return c.done()


def anemone() -> Image.Image:
    c = Canvas(1, 1)
    c.ellipse(8, 12, 5, 3, PURPLE)
    for n in range(9):
        a = math.pi * (0.1 + 0.8 * n / 8)
        c.line(8, 11, 8 - math.cos(a) * 6, 11 - math.sin(a) * 8, TEAL[2])
        c.dot(8 - math.cos(a) * 6, 11 - math.sin(a) * 8, TEAL[3])
    return c.done()


def seaweed() -> Image.Image:
    c = Canvas(1, 2)
    for x0, ramp in ((5, GREEN), (10, TEAL)):
        for y in range(4, 31):
            c.dot(x0 + round(1.6 * math.sin(y / 3 + x0)), y, ramp[2 if y % 4 else 3])
            c.dot(x0 + 1 + round(1.6 * math.sin(y / 3 + x0)), y, ramp[1])
    return c.done()


def bubbles() -> Image.Image:
    c = Canvas(1, 1)
    for x, y, r in ((11, 3, 1.8), (5, 7, 2.3), (12, 10, 1.4), (7, 13, 1.2)):
        c.ellipse(x, y, r, r, [SEA[2], BLUE[3], WHITE[3], WHITE[3]])
    return c.done()


def tunnel_glass() -> Image.Image:
    image = Image.new("RGBA", (16, 16))
    for y in range(16):
        for x in range(16):
            edge = x in (0, 15) or y in (0, 15)
            image.putpixel((x, y), (110, 190, 220, 255) if edge else (170, 228, 246, 255))
    for x, y in ((2, 2), (3, 2), (2, 3), (11, 12), (12, 11)):
        image.putpixel((x, y), (235, 250, 255, 255))
    return image


# -- Valentine's, and a star -------------------------------------------------------------------------------------------
def heart_arch() -> Image.Image:
    c = Canvas(3, 3)
    for a10 in range(0, 1800, 4):
        a = a10 / 1800 * math.pi
        for rr in (17, 18, 19):
            c.dot(24 - rr * math.cos(a), 30 - rr * math.sin(a) * 1.2, GREEN[1 if rr == 19 else 2])
    for n in range(9):
        a = n / 8 * math.pi
        c.ellipse(24 - 18 * math.cos(a), 30 - 18 * math.sin(a) * 1.2, 2.2, 2.2, RED if n % 2 else PINK)
    for x in (6, 42):
        c.rect(x - 2, 30, x + 2, 47, WHITE)
    return c.done()


def sparkle() -> Image.Image:
    c = Canvas(1, 1)
    for d in range(0, 7):
        for x, y in ((8 + d, 8), (8 - d, 8), (8, 8 + d), (8, 8 - d)):
            c.dot(x, y, GOLD[3 if d < 3 else 2])
    for d in range(1, 3):
        for x, y in ((8 + d, 8 + d), (8 - d, 8 - d), (8 + d, 8 - d), (8 - d, 8 + d)):
            c.dot(x, y, GOLD[2])
    return c.done()


# -- travel (the user, 2026-11-17: "里面也要有道路和交通工具，比如可以搭乘飞机，天空之城的船，摆渡，还有rotate的火车等") --------
def train_engine() -> Image.Image:
    c = Canvas(2, 1)
    c.rect(2, 4, 28, 13, GREEN)
    c.rect(20, 1, 29, 8, GREEN)                                  # its cab
    c.rect(22, 2, 27, 5, BLUE)
    c.rect(5, 2, 9, 5, DARK)                                     # its chimney
    c.ellipse(7, 1, 2.5, 1.5, WHITE)
    c.fill(2, 10, 29, 11, GOLD[2])
    for x in (6, 13, 20, 26):
        c.ellipse(x, 14, 2, 2, DARK)
    c.poly([(0, 13), (3, 7), (3, 13)], RED)
    return c.done()


def train_carriage() -> Image.Image:
    c = Canvas(2, 1)
    c.rect(1, 3, 31, 13, RED)
    for x in range(4, 28, 6):
        c.rect(x, 5, x + 4, 9, BLUE)
    c.fill(1, 10, 31, 11, GOLD[2])
    for x in (6, 26):
        c.ellipse(x, 14, 2, 2, DARK)
    return c.done()


def train_station() -> Image.Image:
    c = Canvas(3, 2)
    c.rect(2, 12, 46, 30, CREAM)
    for x in range(6, 44, 8):
        c.rect(x, 16, x + 4, 22, BLUE)
    c.rect(20, 20, 28, 30, WOOD)
    c.poly([(0, 13), (8, 3), (40, 3), (48, 13)], RED)
    c.ellipse(24, 7, 3, 3, WHITE)
    c.line(24, 7, 24, 5, OUTLINE)
    c.line(24, 7, 26, 7, OUTLINE)
    return c.done()


def ferry() -> Image.Image:
    c = Canvas(3, 2)
    c.poly([(2, 18), (46, 18), (40, 28), (8, 28)], RED)          # its hull
    c.fill(4, 20, 44, 21, WHITE[2])
    c.rect(12, 8, 36, 18, WHITE)                                 # its cabin
    for x in range(15, 34, 5):
        c.rect(x, 11, x + 3, 14, BLUE)
    c.rect(28, 2, 33, 8, GOLD)                                   # its funnel
    c.fill(28, 4, 33, 5, OUTLINE)
    return c.done()


def pier() -> Image.Image:
    c = Canvas(2, 1)
    c.rect(0, 3, 32, 11, WOOD)
    for x in range(0, 32, 4):
        c.line(x, 3, x, 10, WOOD[0])
    for x in (2, 14, 26):
        c.rect(x, 11, x + 3, 16, WOOD)
    return c.done()


def airplane() -> Image.Image:
    c = Canvas(3, 3)
    c.poly([(24, 2), (28, 8), (28, 40), (24, 46), (20, 40), (20, 8)], WHITE)        # its body, nose up
    c.poly([(2, 22), (46, 22), (46, 27), (28, 27), (20, 27), (2, 27)], STEEL)        # its wings
    c.poly([(14, 40), (34, 40), (34, 44), (14, 44)], STEEL)                         # its tail
    c.rect(22, 6, 27, 10, BLUE)
    c.fill(21, 14, 28, 15, RED[2])
    c.ellipse(12, 28, 2, 2, DARK)
    c.ellipse(36, 28, 2, 2, DARK)
    return c.done()


def helipad() -> Image.Image:
    c = Canvas(2, 2)
    c.ellipse(16, 16, 14, 14, STEEL)
    c.ellipse(16, 16, 11, 11, [STEEL[0], STEEL[0], STEEL[1], STEEL[1]])
    for y in range(9, 24):
        c.dot(11, y, GOLD[2])
        c.dot(21, y, GOLD[2])
    for x in range(11, 22):
        c.dot(x, 16, GOLD[2])
    return c.done()


def airship() -> Image.Image:
    c = Canvas(3, 2)
    c.ellipse(24, 9, 21, 8, PURPLE)                              # its balloon
    for x in range(8, 41, 8):
        c.line(x, 9, x, 10, PURPLE[0])
    c.line(14, 16, 18, 22, WOOD[0])
    c.line(34, 16, 30, 22, WOOD[0])
    c.poly([(12, 22), (36, 22), (32, 29), (16, 29)], WOOD)       # its gondola, a boat in the sky
    c.poly([(44, 6), (47, 2), (47, 13), (44, 11)], GOLD)         # its fin
    return c.done()


def sky_dock() -> Image.Image:
    c = Canvas(2, 2)
    c.poly([(2, 14), (30, 14), (26, 24), (6, 24)], WOOD)
    c.ellipse(16, 26, 10, 4, WHITE)                              # the cloud it stands on
    for x in (4, 28):
        c.line(x, 4, x, 14, WOOD[0])
        c.poly([(x, 4), (x + 4, 6), (x, 8)], BLUE)
    return c.done()


# -- where people work, learn and live (the user, 2026-11-17: "还要有人类常见的场景，比如摩天大楼，高架桥，学校教学楼，实验室，
# 办公场所，公寓里等") ----------------------------------------------------------------------------------------------------
def whiteboard() -> Image.Image:
    c = Canvas(2, 1)
    c.rect(1, 2, 31, 13, STEEL)
    c.fill(3, 4, 29, 11, WHITE[3])
    c.line(5, 6, 14, 6, BLUE[1]); c.line(5, 8, 11, 8, BLUE[1]); c.line(17, 6, 26, 9, RED[1]); c.line(17, 9, 22, 6, GREEN[1])
    c.fill(10, 13, 22, 14, STEEL[1])
    return c.done()


def school_desk() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(2, 5, 14, 10, WOOD)
    c.line(3, 10, 3, 14, STEEL[1]); c.line(12, 10, 12, 14, STEEL[1])
    c.rect(5, 6, 9, 8, WHITE)
    c.line(10, 6, 12, 8, GOLD[1])
    return c.done()


def lab_bench() -> Image.Image:
    c = Canvas(2, 1)
    c.rect(1, 6, 31, 15, WHITE)
    c.fill(1, 6, 31, 8, (60, 64, 74))
    for x, ramp in ((5, GREEN), (11, BLUE), (17, PURPLE)):
        c.rect(x, 1, x + 3, 6, [(150, 170, 180), (190, 210, 220), (220, 236, 244), (250, 255, 255)])
        c.fill(x, 3, x + 3, 6, ramp[2])
    c.ellipse(25, 4, 3, 2.5, [(150, 170, 180), (190, 210, 220), (220, 236, 244), (250, 255, 255)])
    c.fill(23, 4, 28, 6, ORANGE[2])
    return c.done()


def microscope() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(4, 13, 13, 15, STEEL)
    c.line(6, 13, 6, 6, STEEL[0], 2)
    c.line(6, 6, 10, 3, STEEL[2], 2)
    c.rect(8, 8, 13, 10, STEEL)
    c.ellipse(10, 2.5, 1.5, 1.5, DARK)
    return c.done()


def computer() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(2, 2, 14, 11, DARK)
    c.fill(3, 3, 13, 10, BLUE[2])
    c.line(4, 5, 10, 5, WHITE[3]); c.line(4, 7, 8, 7, WHITE[3])
    c.rect(7, 11, 9, 13, STEEL)
    c.rect(3, 13, 13, 15, STEEL)
    return c.done()


def cubicle() -> Image.Image:
    c = Canvas(2, 2)
    c.rect(1, 4, 31, 8, [(60, 70, 90), (90, 104, 130), (120, 136, 164), (160, 176, 200)])
    c.rect(1, 4, 5, 30, [(60, 70, 90), (90, 104, 130), (120, 136, 164), (160, 176, 200)])
    c.rect(8, 12, 30, 20, WOOD)
    c.rect(18, 9, 27, 15, DARK); c.fill(19, 10, 26, 14, BLUE[2])
    c.ellipse(14, 25, 4, 3, DARK)
    c.line(14, 27, 14, 30, STEEL[1])
    c.ellipse(10, 14, 2, 1.5, GREEN)
    return c.done()


def water_cooler() -> Image.Image:
    c = Canvas(1, 2)
    c.ellipse(8, 7, 5, 6, [(90, 160, 210), (130, 196, 236), (170, 220, 250), (220, 245, 255)])
    c.rect(3, 13, 13, 31, WHITE)
    c.fill(6, 17, 10, 19, BLUE[1])
    return c.done()


def printer() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(1, 6, 15, 13, STEEL)
    c.fill(4, 3, 12, 6, WHITE[3])
    c.fill(4, 12, 12, 14, WHITE[3])
    c.dot(12, 8, GREEN[3])
    return c.done()


def lockers() -> Image.Image:
    c = Canvas(2, 2)
    for x in range(1, 31, 6):
        c.rect(x, 2, x + 6, 31, BLUE)
        c.fill(x + 1, 5, x + 5, 6, BLUE[0]); c.fill(x + 1, 8, x + 5, 9, BLUE[0])
        c.dot(x + 4, 16, GOLD[3])
    return c.done()


def bunk_bed() -> Image.Image:
    c = Canvas(2, 2)
    for y0 in (4, 18):
        c.rect(2, y0, 30, y0 + 9, WOOD)
        c.fill(4, y0 + 2, 28, y0 + 6, WHITE[2])
        c.rect(4, y0 + 1, 10, y0 + 5, WHITE)
        c.fill(12, y0 + 3, 28, y0 + 6, BLUE[2])
    c.line(2, 2, 2, 31, WOOD[0], 2); c.line(29, 2, 29, 31, WOOD[0], 2)
    return c.done()


def kitchenette() -> Image.Image:
    c = Canvas(2, 1)
    c.rect(1, 5, 31, 15, WHITE)
    c.fill(1, 5, 31, 7, STEEL[2])
    c.ellipse(8, 6, 3, 1.2, STEEL)
    c.rect(18, 1, 22, 5, RED); c.rect(24, 2, 29, 5, WHITE)
    for x in (4, 12, 20, 28):
        c.dot(x, 10, STEEL[0])
    return c.done()


def server_rack() -> Image.Image:
    c = Canvas(1, 2)
    c.rect(2, 1, 14, 31, DARK)
    for y in range(3, 30, 3):
        c.fill(4, y, 12, y + 2, (40, 44, 54))
        c.dot(11, y, GREEN[3] if y % 2 else BLUE[3])
    return c.done()


def tower(w: int, h: int, glass: bool, seed: int) -> Image.Image:
    """A skyscraper, w x h tiles: its face in floors of windows, lit here and there; a roof, an aerial."""
    rnd = random.Random(seed)
    c = Canvas(w, h)
    W, H = w * 16, h * 16
    body = [(40, 70, 110), (60, 100, 150), (90, 140, 190), (150, 196, 230)] if glass else [(110, 60, 50), (150, 84, 66), (184, 110, 84), (214, 150, 120)]
    c.rect(2, 8, W - 2, H, body)
    for y in range(12, H - 6, 5):
        for x in range(5, W - 5, 4):
            lit = rnd.random() < 0.25
            c.fill(x, y, x + 2, y + 3, (GOLD[3] if lit else (BLUE[3] if glass else CREAM[1])))
    c.rect(0, 5, W, 9, STEEL)
    c.line(W // 2, 0, W // 2, 5, STEEL[0])
    c.dot(W // 2, 0, RED[3])
    c.rect(W // 2 - 4, H - 6, W // 2 + 4, H, DARK)
    return c.done()


# -- THREE THEMES MORE (the user, 2026-11-21: "城堡/塔楼（中世纪风）； 玩具城（粉色，白色主题）； 中国风（木质建筑/中式庭院/竹林)")
STONE = [(78, 80, 90), (118, 120, 130), (156, 158, 166), (196, 198, 204)]
MOSSY = [(70, 84, 64), (104, 120, 92), (140, 156, 120), (178, 192, 150)]
BLUSH = [(214, 120, 160), (240, 160, 192), (252, 200, 220), (255, 232, 242)]
MINT = [(70, 160, 140), (110, 200, 176), (160, 230, 206), (210, 250, 236)]
SKY = [(90, 140, 210), (130, 180, 240), (176, 214, 255), (220, 238, 255)]
BAMBOO = [(48, 96, 40), (84, 140, 56), (130, 184, 80), (186, 222, 120)]
GLAZE = [(26, 70, 72), (40, 104, 104), (64, 140, 136), (110, 182, 172)]   # a glazed roof's tiles
LACQUER = [(110, 20, 24), (160, 32, 32), (204, 52, 44), (236, 96, 80)]
STRAW = [(150, 112, 40), (196, 154, 60), (228, 196, 96), (248, 228, 150)]


def ground_tile(base: Sequence[Colour], seed: int, speck: Sequence[Colour] = (), stones: bool = False) -> Image.Image:
    """A ground tile, 16 px, no outline - its base with a little grain, specks (flowers) or flagstones' joints."""
    rnd = random.Random(seed)
    c = Canvas(1, 1)
    c.fill(0, 0, 16, 16, base[2])
    for _ in range(14):
        c.dot(rnd.randrange(16), rnd.randrange(16), base[rnd.choice((1, 3))])
    if stones:                                     # flagstones: joints in a running bond
        for y in (0, 8):
            c.line(0, y, 15, y, base[0])
        for x, y0 in ((5, 1), (13, 1), (1, 9), (9, 9)):
            c.line(x, y0, x, y0 + 6, base[0])
    for _ in range(3 if speck else 0):
        x, y = rnd.randrange(1, 15), rnd.randrange(1, 15)
        c.dot(x, y, speck[2]); c.dot(x + 1, y, speck[3]); c.dot(x, y + 1, speck[1])
    return c.done(outline=False)


# castles and towers
def castle_tower() -> Image.Image:
    c = Canvas(2, 4)
    c.rect(6, 12, 26, 64, STONE)
    for y in range(16, 62, 6):                      # its courses
        c.line(7, y, 25, y, STONE[1])
    for x in range(4, 28, 6):                       # the battlements
        c.rect(x, 7, x + 4, 13, STONE)
    c.rect(4, 11, 28, 14, STONE)
    for y in (22, 38):                              # arrow slits
        c.fill(15, y, 17, y + 7, DARK[0])
    c.line(16, 0, 16, 8, WOOD[0])
    c.poly([(17, 1), (26, 3), (17, 6)], RED)
    c.rect(11, 52, 21, 64, WOOD)
    c.ellipse(16, 52, 5, 3, WOOD)
    return c.done()


def castle_wall() -> Image.Image:
    c = Canvas(2, 2)
    c.rect(0, 10, 32, 32, STONE)
    for y in range(14, 31, 5):
        c.line(0, y, 31, y, STONE[1])
        for x in range(3 + (y // 5 % 2) * 4, 32, 8):
            c.line(x, y - 4, x, y, STONE[1])
    for x in range(0, 32, 8):
        c.rect(x, 4, x + 5, 11, STONE)
    return c.done()


def castle_gate() -> Image.Image:
    c = Canvas(3, 3)
    c.rect(10, 14, 38, 48, STONE)
    for x in range(10, 38, 6):
        c.rect(x, 9, x + 4, 15, STONE)
    for x0 in (0, 34):                              # its two towers
        c.rect(x0, 6, x0 + 14, 48, STONE)
        for x in range(x0, x0 + 14, 5):
            c.rect(x, 1, x + 3, 7, STONE)
        c.fill(x0 + 6, 18, x0 + 8, 25, DARK[0])
    c.ellipse(24, 30, 9, 8, DARK)                   # the arch, the portcullis down in it
    c.fill(15, 30, 33, 48, DARK[0])
    for x in range(17, 33, 3):
        c.line(x, 24, x, 47, STEEL[1])
    for y in range(28, 47, 4):
        c.line(16, y, 32, y, STEEL[1])
    c.poly([(21, 10), (27, 10), (24, 15)], GOLD)
    return c.done()


def banner_pole() -> Image.Image:
    c = Canvas(1, 2)
    c.line(4, 2, 4, 31, WOOD[0], 2)
    c.ellipse(5, 2, 2, 2, GOLD)
    c.rect(6, 4, 14, 20, RED)
    c.poly([(6, 19), (14, 19), (10, 24)], RED)
    c.ellipse(10, 11, 2.5, 2.5, GOLD)
    return c.done()


def knight_statue() -> Image.Image:
    c = Canvas(1, 2)
    c.rect(2, 25, 14, 32, STONE)
    c.rect(5, 12, 11, 25, STEEL)                    # armour
    c.ellipse(8, 8, 3.5, 4, STEEL)                  # helm, its visor
    c.line(6, 8, 10, 8, DARK[0])
    c.line(12, 2, 12, 22, WHITE[2])                 # the sword held up
    c.line(10, 16, 14, 16, GOLD[2])
    c.rect(2, 13, 6, 21, BLUE)                      # the shield
    c.dot(4, 16, GOLD[3])
    return c.done()


def stone_well() -> Image.Image:
    c = Canvas(1, 2)
    c.line(3, 8, 3, 22, WOOD[0], 2); c.line(12, 8, 12, 22, WOOD[0], 2)
    c.poly([(0, 9), (8, 2), (16, 9)], RED)
    c.line(4, 14, 12, 14, WOOD[1])
    c.line(8, 14, 8, 18, WHITE[0])
    c.rect(7, 18, 10, 21, WOOD)
    c.rect(1, 22, 15, 31, STONE)
    c.fill(3, 22, 13, 24, SEA[0])
    return c.done()


def training_dummy() -> Image.Image:
    c = Canvas(1, 2)
    c.line(8, 10, 8, 30, WOOD[0], 2)
    c.line(2, 14, 14, 14, WOOD[1], 2)
    c.rect(5, 12, 12, 24, STRAW)
    c.ellipse(8, 7, 4, 4, STRAW)
    c.dot(7, 6, DARK[0]); c.dot(10, 6, DARK[0])
    c.rect(4, 29, 13, 32, WOOD)
    return c.done()


def weapon_rack() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(1, 12, 15, 15, WOOD)
    c.line(2, 3, 2, 12, WOOD[0]); c.line(13, 3, 13, 12, WOOD[0])
    c.line(1, 4, 14, 4, WOOD[1])
    for x, ramp in ((5, STEEL), (8, STEEL), (11, WOOD)):
        c.line(x, 1, x, 12, ramp[2])
    c.poly([(4, 0), (6, 0), (5, 2)], STEEL)
    return c.done()


def haystack() -> Image.Image:
    c = Canvas(1, 1)
    c.ellipse(8, 9, 7, 6, STRAW)
    for x in range(3, 14, 3):
        c.line(x, 6, x + 1, 14, STRAW[1])
    return c.done()


def horse() -> Image.Image:
    c = Canvas(1, 1)
    c.ellipse(8, 9, 5, 3.2, WOOD)                   # its body
    c.line(4, 11, 4, 15, WOOD[0]); c.line(7, 11, 7, 15, WOOD[0])
    c.line(10, 11, 10, 15, WOOD[0]); c.line(12, 11, 12, 15, WOOD[0])
    c.rect(11, 3, 15, 8, WOOD)                      # its neck and head
    c.line(10, 3, 12, 8, DARK[1], 1)                # mane
    c.dot(14, 5, DARK[0])
    c.line(3, 8, 1, 12, DARK[1])                    # tail
    return c.done()


def carriage() -> Image.Image:
    c = Canvas(2, 1)
    c.rect(4, 2, 28, 11, RED)
    c.fill(8, 4, 13, 8, SKY[3]); c.fill(18, 4, 23, 8, SKY[3])
    c.rect(2, 1, 30, 3, GOLD)
    for x in (8, 24):
        c.ellipse(x, 13, 3, 3, WOOD)
        c.dot(x, 13, GOLD[3])
    return c.done()


# a toy town in pink and white
def pink_tree() -> Image.Image:
    c = Canvas(2, 2)
    c.rect(14, 18, 18, 31, WHITE)
    for x, y, r in ((10, 13, 7), (22, 13, 7), (16, 8, 8), (16, 15, 8)):
        c.ellipse(x, y, r, r * 0.85, BLUSH)
    for x, y in ((9, 10), (21, 7), (15, 15), (24, 15)):
        c.dot(x, y, WHITE[3])
    return c.done()


def candy_cane() -> Image.Image:
    c = Canvas(1, 2)
    for y in range(8, 31):
        c.fill(7, y, 10, y + 1, RED[2] if (y // 3) % 2 else WHITE[3])
    for a in range(0, 181, 12):
        x, y = 6 + 4 * math.cos(math.radians(a)), 8 - 4 * math.sin(math.radians(a))
        c.fill(x - 1, y - 1, x + 2, y + 1, RED[2] if (a // 36) % 2 else WHITE[3])
    return c.done()


def lollipop() -> Image.Image:
    c = Canvas(1, 2)
    c.line(8, 14, 8, 31, WHITE[2], 2)
    c.ellipse(8, 8, 6.5, 6.5, BLUSH)
    for a in range(0, 720, 30):                     # its swirl
        r = a / 720 * 6
        c.dot(8 + r * math.cos(math.radians(a)), 8 + r * math.sin(math.radians(a)), WHITE[3])
    return c.done()


def toy_blocks() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(1, 8, 8, 15, BLUSH); c.rect(8, 8, 15, 15, SKY); c.rect(4, 1, 11, 8, MINT)
    c.dot(4, 11, WHITE[3]); c.dot(11, 11, WHITE[3]); c.dot(7, 4, WHITE[3])
    return c.done()


def teddy_bear() -> Image.Image:
    c = Canvas(1, 1)
    c.ellipse(8, 11, 5, 4.5, CREAM)
    c.ellipse(8, 5, 4, 3.6, CREAM)
    c.ellipse(4.5, 2.5, 1.6, 1.6, CREAM); c.ellipse(11.5, 2.5, 1.6, 1.6, CREAM)
    c.dot(7, 5, DARK[0]); c.dot(9, 5, DARK[0]); c.dot(8, 6, WOOD[0])
    c.fill(6, 8, 11, 9, BLUSH[1])                   # a pink bow
    return c.done()


def toy_soldier() -> Image.Image:
    c = Canvas(1, 2)
    c.rect(5, 1, 11, 9, DARK)                       # the busby
    c.ellipse(8, 11, 3, 2.6, CREAM)
    c.dot(6, 12, BLUSH[1]); c.dot(10, 12, BLUSH[1])
    c.rect(5, 14, 11, 23, BLUSH)
    c.line(5, 17, 10, 17, WHITE[3])
    c.rect(5, 23, 11, 30, WHITE)
    c.rect(4, 30, 12, 32, DARK)
    return c.done()


def cupcake() -> Image.Image:
    c = Canvas(1, 1)
    c.poly([(3, 9), (13, 9), (11, 15), (5, 15)], WHITE)
    c.ellipse(8, 7, 6, 3.6, BLUSH)
    c.ellipse(8, 3, 2, 2, RED)
    return c.done()


def white_fence() -> Image.Image:
    c = Canvas(1, 1)
    c.line(0, 7, 15, 7, WHITE[1]); c.line(0, 11, 15, 11, WHITE[1])
    for x in (2, 7, 12):
        c.rect(x, 3, x + 3, 15, WHITE)
        c.dot(x + 1, 2, WHITE[3])
    return c.done()


def toy_engine() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(1, 5, 11, 12, BLUSH)
    c.rect(9, 1, 15, 12, WHITE)
    c.fill(11, 3, 14, 6, SKY[3])
    c.rect(3, 1, 6, 5, MINT)
    for x in (4, 11):
        c.ellipse(x, 13, 2.2, 2.2, DARK)
    return c.done()


def toy_car() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(1, 4, 15, 12, WHITE)
    c.fill(3, 6, 13, 9, BLUSH[2])
    for x in (4, 12):
        c.ellipse(x, 13, 2.2, 2.2, DARK)
    return c.done()


# old China: wood, a courtyard, bamboo
def bamboo() -> Image.Image:
    c = Canvas(1, 2)
    for x, top in ((4, 2), (8, 0), (12, 4)):
        c.line(x, top, x, 31, BAMBOO[1], 2)
        for y in range(top + 4, 31, 6):
            c.line(x - 1, y, x + 2, y, BAMBOO[0])
        for y in range(top + 2, 24, 7):            # its leaves
            c.line(x + 1, y, x + 5, y - 2, BAMBOO[3]); c.line(x, y + 1, x - 4, y - 1, BAMBOO[2])
    return c.done()


def bamboo_grove() -> Image.Image:
    c = Canvas(2, 2)
    rnd = random.Random(11)
    for x in range(3, 30, 4):
        top = rnd.randrange(0, 6)
        c.line(x, top, x, 31, BAMBOO[rnd.choice((1, 2))], 2)
        for y in range(top + 3, 31, 5):
            c.line(x - 1, y, x + 2, y, BAMBOO[0])
        for y in range(top + 1, 22, 6):
            c.line(x + 1, y, x + 5, y - 2, BAMBOO[3]); c.line(x, y + 1, x - 4, y - 1, BAMBOO[2])
    return c.done()


def eaves(c: Canvas, x0: float, x1: float, y: float, depth: float, ramp: Sequence[Colour]) -> None:
    """A Chinese roof's sweep: up at both ends."""
    c.poly([(x0 - 3, y - 2), (x0 + 3, y), (x1 - 3, y), (x1 + 3, y - 2), (x1 - 1, y + depth), (x0 + 1, y + depth)], ramp)
    for x in range(int(x0), int(x1), 3):
        c.line(x, y + 1, x, y + depth - 1, ramp[1])


def pagoda() -> Image.Image:
    c = Canvas(2, 4)
    c.line(16, 0, 16, 6, GOLD[1])
    c.ellipse(16, 4, 1.5, 1.5, GOLD)
    for n, (y, half) in enumerate(((6, 6), (18, 9), (30, 11), (42, 13))):
        c.rect(16 - half + 3, y + 4, 16 + half - 3, y + 12, LACQUER)
        c.fill(15, y + 6, 18, y + 12, DARK[0])
        eaves(c, 16 - half, 16 + half, y + 2, 4, GLAZE)
    c.rect(2, 56, 30, 64, STONE)
    return c.done()


def pavilion() -> Image.Image:
    c = Canvas(3, 3)
    eaves(c, 6, 42, 12, 8, GLAZE)
    c.poly([(16, 4), (32, 4), (40, 13), (8, 13)], GLAZE)
    c.ellipse(24, 3, 2, 2, GOLD)
    for x in (10, 20, 28, 37):
        c.line(x, 20, x, 42, LACQUER[2], 2)
    c.line(9, 34, 39, 34, LACQUER[1])               # its railing
    c.rect(4, 42, 44, 48, STONE)
    return c.done()


def moon_gate() -> Image.Image:
    c = Canvas(3, 3)
    c.rect(0, 10, 48, 48, WHITE)                    # a white wall, its tiled top
    eaves(c, 0, 48, 6, 5, GLAZE)
    for y in range(int(16), 48):
        for x in range(48):
            if (x + 0.5 - 24) ** 2 / 15.5 ** 2 + (y + 0.5 - 31) ** 2 / 16.5 ** 2 < 1:
                c.dot(x, y, BAMBOO[2] if y > 40 else SKY[3])
    for a in range(0, 360, 4):                      # the round gate's stone rim
        c.dot(24 + 16 * math.cos(math.radians(a)), 31 + 17 * math.sin(math.radians(a)), STONE[1])
    return c.done()


def stone_lantern() -> Image.Image:
    c = Canvas(1, 2)
    c.poly([(1, 7), (8, 1), (15, 7)], STONE)
    c.rect(3, 8, 13, 16, STONE)
    c.fill(5, 10, 11, 14, GOLD[3])
    c.rect(6, 16, 10, 26, STONE)
    c.rect(2, 26, 14, 31, STONE)
    return c.done()


def scholar_rock() -> Image.Image:
    c = Canvas(1, 2)
    c.poly([(4, 2), (11, 0), (14, 9), (10, 16), (13, 25), (3, 27), (5, 15), (2, 8)], STONE)
    for x, y in ((8, 6), (6, 13), (9, 20)):        # its holes
        c.ellipse(x, y, 1.6, 1.4, DARK)
    c.rect(2, 26, 14, 31, WOOD)
    return c.done()


def willow() -> Image.Image:
    c = Canvas(2, 3)
    c.line(16, 47, 16, 18, WOOD[0], 3)
    c.ellipse(16, 14, 13, 9, BAMBOO)
    rnd = random.Random(5)
    for x in range(4, 29, 2):                       # its long hanging strands
        c.line(x, 12, x + rnd.choice((-1, 0, 1)), 22 + rnd.randrange(14), BAMBOO[rnd.choice((2, 3))])
    return c.done()


def paifang() -> Image.Image:
    c = Canvas(3, 3)
    for x in (6, 40):
        c.rect(x, 12, x + 4, 46, LACQUER)
        c.rect(x - 2, 44, x + 6, 48, STONE)
    eaves(c, 2, 46, 6, 6, GLAZE)
    c.rect(8, 14, 40, 20, LACQUER)
    c.rect(17, 15, 31, 19, GOLD)
    c.line(19, 17, 29, 17, LACQUER[0])
    return c.done()


def bonsai() -> Image.Image:
    c = Canvas(1, 1)
    c.rect(3, 11, 13, 15, BLUE)
    c.line(8, 11, 6, 6, WOOD[0]); c.line(6, 6, 10, 3, WOOD[0])
    c.ellipse(5, 6, 3.5, 2, BAMBOO); c.ellipse(11, 3, 3.5, 2, BAMBOO)
    return c.done()


def dragon_boat() -> Image.Image:
    c = Canvas(2, 1)
    c.poly([(1, 7), (30, 7), (27, 13), (4, 13)], LACQUER)
    c.line(4, 9, 27, 9, GOLD[3])
    c.ellipse(29, 5, 3, 3, GOLD)                    # the dragon's head
    c.dot(30, 4, DARK[0])
    for x in range(7, 26, 4):                       # its rowers
        c.ellipse(x, 5, 1.5, 1.5, CREAM)
    return c.done()


def rickshaw() -> Image.Image:
    c = Canvas(1, 1)
    c.poly([(3, 2), (11, 2), (12, 8), (3, 8)], LACQUER)
    c.rect(3, 8, 12, 11, LACQUER)
    c.line(12, 10, 16, 12, WOOD[0])
    c.ellipse(7, 12, 3.2, 3.2, DARK)
    c.dot(7, 12, GOLD[3])
    return c.done()


THEME_THINGS: List[Tuple[str, Callable[[], Image.Image]]] = [
    ("toy_pink", lambda: ground_tile(BLUSH, 1, WHITE)), ("toy_pink2", lambda: ground_tile(BLUSH, 2, WHITE)),
    ("toy_white", lambda: ground_tile(WHITE, 3, BLUSH)), ("toy_white_path", lambda: ground_tile(WHITE, 4, stones=True)),
    ("toy_white_stone", lambda: ground_tile(BLUSH, 5, stones=True)),
    ("flagstone", lambda: ground_tile(STONE, 6, stones=True)), ("flagstone2", lambda: ground_tile(MOSSY, 7, stones=True)),
    ("castle_tower", castle_tower), ("castle_wall", castle_wall), ("castle_gate", castle_gate),
    ("banner_pole", banner_pole), ("knight_statue", knight_statue), ("stone_well", stone_well),
    ("training_dummy", training_dummy), ("weapon_rack", weapon_rack), ("haystack", haystack), ("horse", horse),
    ("carriage", carriage),
    ("pink_tree", pink_tree), ("candy_cane", candy_cane), ("lollipop", lollipop), ("toy_blocks", toy_blocks),
    ("teddy_bear", teddy_bear), ("toy_soldier", toy_soldier), ("cupcake", cupcake), ("white_fence", white_fence),
    ("toy_engine", toy_engine), ("toy_car", toy_car),
    ("bamboo", bamboo), ("bamboo_grove", bamboo_grove), ("pagoda", pagoda), ("pavilion", pavilion),
    ("moon_gate", moon_gate), ("stone_lantern", stone_lantern), ("scholar_rock", scholar_rock), ("willow", willow),
    ("paifang", paifang), ("bonsai", bonsai), ("dragon_boat", dragon_boat), ("rickshaw", rickshaw),
]


def main() -> None:
    fishes: List[Image.Image] = []
    sea = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if sea is not None and sea.is_file():
        sheet = Image.open(sea).convert("RGBA")
        fishes = [sheet.crop((n * 17, 0, n * 17 + 16, 16)) for n in range(0, sheet.width // 17 + 1)]
        fishes = [f for f in fishes if f.getbbox()]
    things: List[Tuple[str, Image.Image]] = [
        ("lantern", lantern()), ("firecrackers", firecrackers()), ("fu_sign", fu_sign()), ("red_envelope", red_envelope()),
        ("balloons", balloons()), ("queue_rail", queue_rail()), ("jellyfish", jellyfish()), ("coral", coral()),
        ("anemone", anemone()), ("bubbles", bubbles()), ("tunnel_glass", tunnel_glass()),
        ("coaster_track", coaster_track(False)), ("coaster_track_v", coaster_track(True)), ("coaster_car", coaster_car()),
        ("sparkle", sparkle()), ("seaweed", seaweed()), ("lantern_string", lantern_string()), ("bunting", bunting()),
        ("fish_tank", fish_tank(fishes)), ("big_fish_tank", fish_tank(fishes, 4, 2)),
        ("couplet_door", couplet_door()), ("firework", firework()), ("lion_head", lion_head()), ("plum_tree", plum_tree()),
        ("ticket_booth", ticket_booth()), ("ice_cream_stand", food_stand(PINK, ice_cream)),
        ("hot_dog_stand", food_stand(RED, hot_dogs)), ("candy_stand", food_stand(PURPLE, candy)),
        ("coaster_station", coaster_station()), ("circus_tent", circus_tent()), ("heart_arch", heart_arch()),
        ("swing_ship", swing_ship()), ("carousel", carousel()), ("drop_tower", drop_tower()), ("ferris_wheel", ferris_wheel()),
        ("train_engine", train_engine()), ("train_carriage", train_carriage()), ("train_station", train_station()),
        ("ferry", ferry()), ("pier", pier()), ("airplane", airplane()), ("helipad", helipad()), ("airship", airship()),
        ("sky_dock", sky_dock()),
        ("whiteboard", whiteboard()), ("school_desk", school_desk()), ("lab_bench", lab_bench()), ("microscope", microscope()),
        ("computer", computer()), ("cubicle", cubicle()), ("water_cooler", water_cooler()), ("printer", printer()),
        ("lockers", lockers()), ("bunk_bed", bunk_bed()), ("kitchenette", kitchenette()), ("server_rack", server_rack()),
        ("tower_glass", tower(2, 5, True, 1)), ("tower_brick", tower(2, 4, False, 2)), ("tower_tall", tower(3, 6, True, 3)),
    ] + [(name, make()) for name, make in THEME_THINGS]   # (after all before: their places on the sheet unchanged)
    cols = sum(image.width // 16 for _n, image in things)
    rows = max(image.height // 16 for _n, image in things)
    out = Image.new("RGBA", (cols * 17 - 1, rows * 17 - 1), (0, 0, 0, 0))
    col = 0
    for name, image in things:
        w, h = image.width // 16, image.height // 16
        for ty in range(h):
            for tx in range(w):
                out.paste(image.crop((tx * 16, ty * 16, tx * 16 + 16, ty * 16 + 16)), ((col + tx) * 17, ty * 17))
        print(f'{name}: ["drawn", {col}, 0, {w}, {h}]')
        col += w
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT)


if __name__ == "__main__":
    main()

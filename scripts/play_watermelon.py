"""THE AVATAR AS THE PLAY VIEW'S WALKER (the user, 2026-11-17: "logo就不在显示在屏幕上，但是会显示在游戏视图里（主角的avatar, 有
行走动画什么的)"): the watermelon, in the avatar's own colours (agent/character/watermelon_flat.py), drawn as 16 x 16 pixel
frames - a row each way it faces (down, left, right, up), four frames a row (standing, a step, standing, the other step) -
into personalized_agent/api/web/play/watermelon.png. Made here, by code: run again to change it.

    python scripts/play_watermelon.py [--preview out.png]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "personalized_agent" / "api" / "web" / "play" / "watermelon.png"
SIZE = 16
WAYS = ("down", "left", "right", "up")

# The avatar's colours (watermelon_flat.py), and a darker green for the stripes' edge in so few pixels.
OUTLINE = (67, 127, 119)
BASE = (174, 213, 164)
STRIPE = (119, 180, 169)
LEAF = (153, 210, 192)
LEAF_DARK = (110, 170, 160)
VEIN = (71, 143, 145)
CREAM = (255, 255, 255)
PUPIL = (60, 80, 66)
BLUSH = (240, 150, 150)
MOUTH = (106, 129, 108)
SHINE = (214, 236, 205)


def body(lift: int) -> set:
    """The round body's pixels, `lift` pixels up (a step's bob)."""
    out = set()
    for y in range(SIZE):
        for x in range(SIZE):
            if ((x - 7.5) / 6.6) ** 2 + ((y - 8.2 + lift) / 6.0) ** 2 <= 1:
                out.add((x, y))
    return out


def frame(way: str, step: int) -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    px = image.load()
    lift = 1 if step in (1, 3) else 0
    shape = body(lift)
    # The feet first, under the body: two small leaves, one forward a step at a time.
    if way in ("down", "up"):
        left_up, right_up = (step == 1), (step == 3)
        feet = [(5, 14 - left_up), (4, 14 - left_up), (10, 14 - right_up), (11, 14 - right_up)]
    else:
        front = 1 if step == 1 else (-1 if step == 3 else 0)
        feet = [(6 + front, 14), (7 + front, 14), (9 - front, 14), (10 - front, 14)]
    for n, (x, y) in enumerate(feet):
        px[x, y] = LEAF if n % 2 == 0 else LEAF_DARK
        px[x, y + 1] = VEIN
    for (x, y) in shape:
        edge = any((x + dx, y + dy) not in shape for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        if edge:
            px[x, y] = OUTLINE
            continue
        # Stripes curving round the body, as the avatar's - shifted the way it faces.
        turn = {"left": -1.5, "right": 1.5}.get(way, 0.0)
        u = (x - 7.5 - turn) / (1.0 + 0.12 * abs(y - 8 + lift))
        px[x, y] = STRIPE if (u % 4.2) < 1.3 else BASE
    top = min(y for _x, y in shape)
    # A shine, top left; a sprout on top.
    px[4, top + 2] = SHINE
    px[5, top + 1] = SHINE
    px[7, top - 1] = VEIN
    px[8, top - 1] = VEIN
    px[6, top - 2] = LEAF
    px[9, top - 2] = LEAF
    px[5, top - 2] = LEAF_DARK
    px[10, top - 2] = LEAF_DARK
    # Its face, the way it looks - none from behind.
    eye_y = 8 - lift
    eyes = {"down": (5, 9), "left": (3, 7), "right": (8, 12)}.get(way)
    if eyes:
        for ex in eyes:
            px[ex, eye_y] = CREAM
            px[ex + 1, eye_y] = CREAM
            px[ex, eye_y + 1] = CREAM
            px[ex + 1, eye_y + 1] = PUPIL
        cheeks = {"down": (4, 11), "left": (2, 8), "right": (7, 13)}[way]
        for cx in cheeks:
            px[cx, eye_y + 2] = BLUSH
        mouth = {"down": (7, 8), "left": (5,), "right": (10,)}[way]
        for mx in mouth:
            px[mx, eye_y + 3] = MOUTH
    return image


def sheet() -> Image.Image:
    out = Image.new("RGBA", (SIZE * 4, SIZE * len(WAYS)), (0, 0, 0, 0))
    for row, way in enumerate(WAYS):
        for step in range(4):
            out.paste(frame(way, step), (step * SIZE, row * SIZE))
    return out


if __name__ == "__main__":
    made = sheet()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    made.save(OUT)
    if "--preview" in sys.argv:
        where = Path(sys.argv[sys.argv.index("--preview") + 1])
        back = Image.new("RGBA", made.size, (120, 170, 80, 255))
        back.alpha_composite(made)
        back.resize((made.width * 12, made.height * 12), Image.NEAREST).save(where)
    print(OUT, made.size)

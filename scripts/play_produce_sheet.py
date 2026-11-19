"""THE HARVEST AND THE ROBOTS (the user, 2026-11-18: "你的西瓜什么太不像了，水果/植物的图库重新找，另外加上机器人主题的"):
api/web/play/produce.png laid from three CC0 packs, 16 px tiles with 1 px between, as the other sheets are -

- rows 0-9: "Farming crops 16x16" by josehzz (opengameart.org/content/farming-crops-16x16), as it is laid: two crops a
  row, each its picked crop then its plant ripe, growing, young, a sprout, and seeded;
- rows 10-12: "32 free fruits and veggies" by Crumpaloo (crumpaloo.itch.io/32-free-fruits-and-veggies-asset-pack), each
  found in its sheet and put in a tile of its own, in reading order;
- rows 13-17: "1 bit robots 16x16" by nightgane (opengameart.org/content/1-bit-robots-16x16), white made each its own
  metal - shaded below - its black kept as its outline.

    python scripts/play_produce_sheet.py <Crop_Spritesheet.png> <Fruits And Veggies 2.png> <16x16_1bit_robots_0.png>
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web" / "play" / "produce.png"
COLS, FRUIT_ROW, ROBOT_ROW = 12, 10, 13
#: Each robot's metal, in turn: its face, and below where it is shaded.
METALS = [("#d9dee6", "#9aa4b2"), ("#a9cbe8", "#6f95b8"), ("#f0cf7a", "#c49a3e"), ("#b4e2c6", "#76b08f"),
          ("#e8b0a8", "#b97a72"), ("#c9b8e8", "#9584bd")]


def tile(sheet: Image.Image, col: int, row: int, img: Image.Image) -> None:
    sheet.alpha_composite(img, (col * 17, row * 17))


def fruits(img: Image.Image) -> list:
    """Each fruit of the sheet: the parts drawn, joined where they touch, in reading order (by rows of their middles)."""
    w, h = img.size
    alpha = img.getchannel("A").load()
    seen, boxes = set(), []
    for y in range(h):
        for x in range(w):
            if alpha[x, y] < 20 or (x, y) in seen:
                continue
            stack, box = [(x, y)], [x, y, x, y]
            seen.add((x, y))
            while stack:
                px, py = stack.pop()
                box = [min(box[0], px), min(box[1], py), max(box[2], px), max(box[3], py)]
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = px + dx, py + dy
                        if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and alpha[nx, ny] >= 20:
                            seen.add((nx, ny))
                            stack.append((nx, ny))
            boxes.append(box)
    # A leaf apart from its fruit joins the fruit it is nearest (within a few pixels).
    merged = []
    for box in sorted(boxes, key=lambda b: -(b[2] - b[0] + 1) * (b[3] - b[1] + 1)):
        host = next((m for m in merged if box[0] <= m[2] + 3 and box[2] >= m[0] - 3 and box[1] <= m[3] + 3 and box[3] >= m[1] - 3
                     and max(m[2], box[2]) - min(m[0], box[0]) < 16 and max(m[3], box[3]) - min(m[1], box[1]) < 16), None)
        if host:
            host[:] = [min(host[0], box[0]), min(host[1], box[1]), max(host[2], box[2]), max(host[3], box[3])]
        else:
            merged.append(list(box))
    rows: dict = {}
    for box in merged:
        rows.setdefault(round(((box[1] + box[3]) / 2) / 17), []).append(box)
    out = []
    for key in sorted(rows):
        for box in sorted(rows[key], key=lambda b: b[0]):
            piece = img.crop((box[0], box[1], box[2] + 1, box[3] + 1))
            cell = Image.new("RGBA", (16, 16))
            cell.alpha_composite(piece, ((16 - piece.width) // 2, 16 - piece.height))      # standing on its foot
            out.append(cell)
    return out


def robot(piece: Image.Image, n: int) -> Image.Image:
    face, shade = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in METALS[n % len(METALS)])
    src, out = piece.load(), Image.new("RGBA", piece.size)
    dst = out.load()
    for y in range(piece.height):
        for x in range(piece.width):
            r, g, b, a = src[x, y]
            if a < 20:
                continue
            if r > 128:                                   # its white: metal, shaded where its outline is below
                below = src[x, y + 1] if y + 1 < piece.height else (0, 0, 0, 0)
                dst[x, y] = (*(shade if below[3] < 20 or below[0] < 128 else face), 255)
            else:
                dst[x, y] = (30, 34, 46, 255)
    return out


def main() -> None:
    crops, fruit_sheet, robots = (Image.open(p).convert("RGBA") for p in sys.argv[1:4])
    found = fruits(fruit_sheet)
    assert len(found) == 32, len(found)
    count = robots.width // 16
    rows = ROBOT_ROW + (count + COLS - 1) // COLS
    sheet = Image.new("RGBA", (COLS * 17, rows * 17))
    for r in range(crops.height // 16):
        for c in range(crops.width // 16):
            tile(sheet, c, r, crops.crop((c * 16, r * 16, c * 16 + 16, r * 16 + 16)))
    for n, cell in enumerate(found):
        tile(sheet, n % COLS, FRUIT_ROW + n // COLS, cell)
    for n in range(count):
        tile(sheet, n % COLS, ROBOT_ROW + n // COLS, robot(robots.crop((n * 16, 0, n * 16 + 16, 16)), n))
    sheet.save(OUT)
    print(OUT, sheet.size, len(found), "fruits,", count, "robots")


if __name__ == "__main__":
    main()

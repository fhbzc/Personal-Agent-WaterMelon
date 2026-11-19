"""PROGRESS WIDGETS (the user, 2026-11-19: "里面支持养鸡场，植物种植之类，不同阶段会变化形态，作为控件给模型"; "再加点"): things
of a room or an open ground that change as the work they stand for goes on - a field from seeds to the harvest, a
chicken farm from an empty coop to hens and baskets of eggs, a building site from its footings to its roof. The model
chooses one and places it ("kind": "widget:<name>", for a card or a great ground's stage); WHICH OF ITS STAGES IS
SHOWN IS THE CODE'S - from the card's status as it is now (play.js playWidgetStage): not begun its first, done its
last, under way by how much of the work under it is done, set aside its own withered look. So it moves on by itself,
nothing asked.

Each: its size (columns x rows), where it may stand ("open", "inside", "both"), and what each of its stages looks like
- in that order. The page draws them (play.js PLAY_WIDGETS - the same names and as many stages each).
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

#: The crops of produce.png (scripts/play_produce_sheet.py): each a field of its own - its plants seeded, sprouting,
#: young, growing, ripe, picked.
CROPS = ("turnip", "rose", "cucumber", "tulip", "tomato", "melon", "eggplant", "lemon", "pineapple", "rice", "wheat",
         "grapes", "strawberry", "cassava", "potato", "coffee", "sunflower", "corn", "orange", "avocado")
CROP_STAGES = ("seeded", "sprouting", "young", "growing", "ripe", "harvested")

WIDGETS: Dict[str, Dict[str, object]] = {
    "orchard": {"size": (3, 2), "where": "open",
                "stages": ("saplings", "young trees", "grown trees", "trees in fruit", "baskets of fruit")},
    "chicken_farm": {"size": (3, 2), "where": "open",
                     "stages": ("an empty coop", "eggs in the nests", "chicks", "hens", "hens and baskets of eggs")},
    "pasture": {"size": (4, 3), "where": "open",
                "stages": ("a fence", "hay in", "a few cows and sheep", "a herd", "a herd and milk churns")},
    "apiary": {"size": (2, 2), "where": "open",
               "stages": ("an empty box", "a hive", "hives and bees", "hives and jars of honey")},
    "fish_pond": {"size": (3, 2), "where": "open",
                  "stages": ("a pond dug", "water in", "fry", "fish", "fish and a full basket")},
    "building_site": {"size": (3, 3), "where": "open",
                      "stages": ("footings", "scaffolding", "walls half up", "the roof on", "finished")},
    "rocket_pad": {"size": (2, 3), "where": "open",
                   "stages": ("parts on the pad", "a third stacked", "two thirds stacked", "standing ready", "lifting off")},
    "vineyard": {"size": (3, 2), "where": "open",
                 "stages": ("posts and wires", "vines", "vines in leaf", "grapes", "barrels of wine")},
    "greenhouse": {"size": (3, 2), "where": "open",
                   "stages": ("its frame", "glazed", "seedlings inside", "growing inside", "in bloom")},
    "mushroom_farm": {"size": (2, 2), "where": "both",
                      "stages": ("logs laid", "small mushrooms", "large mushrooms", "a basket of mushrooms")},
    "workbench": {"size": (2, 1), "where": "inside",
                  "stages": ("a crate", "parts laid out", "half assembled", "a working machine")},
    "bookshelf": {"size": (2, 2), "where": "inside",
                  "stages": ("empty shelves", "a few books", "half full", "full")},
    "bakery": {"size": (2, 1), "where": "inside",
               "stages": ("sacks of flour", "dough rising", "the oven glowing", "loaves out")},
    "easel": {"size": (1, 2), "where": "both",
              "stages": ("a blank canvas", "a sketch", "in colour", "framed")},
    "writing_desk": {"size": (2, 1), "where": "inside",
                     "stages": ("a blank page", "a draft", "a stack of pages", "a bound book")},
    "forge": {"size": (2, 1), "where": "inside",
              "stages": ("ore", "the fire lit", "ingots", "tools made")},
    "aquarium": {"size": (2, 1), "where": "inside",
                 "stages": ("an empty tank", "water in", "small fish", "many fish")},
    "loom": {"size": (2, 1), "where": "inside",
             "stages": ("bare threads", "a few rows woven", "half woven", "a finished cloth")},
    "kiln": {"size": (2, 2), "where": "both",
             "stages": ("clay", "pots shaped", "the kiln firing", "glazed pots")},
    "telescope": {"size": (2, 2), "where": "both",
                  "stages": ("its parts", "its mount", "its tube on", "turned to the stars")},
}
for _crop in CROPS:
    WIDGETS[f"field_{_crop}"] = {"size": (3, 2), "where": "open", "stages": CROP_STAGES, "crop": _crop}


def widget_of(kind: str) -> Optional[str]:
    """The widget a kind names ("widget:chicken_farm" -> "chicken_farm"), else None."""
    if isinstance(kind, str) and kind.startswith("widget:") and kind[7:] in WIDGETS:
        return kind[7:]
    return None


def size_of(name: str) -> Tuple[int, int]:
    return tuple(WIDGETS[name]["size"])  # type: ignore[return-value]


def widgets_told(open_air: bool) -> str:
    """The widgets a place may use, for its design: each its name, its size and its stages in order."""
    out = []
    for name, one in WIDGETS.items():
        if name.startswith("field_"):
            continue
        if one["where"] == ("inside" if open_air else "open"):
            continue
        w, h = one["size"]
        out.append(f"widget:{name} {w}x{h} ({' -> '.join(one['stages'])})")
    if open_air:
        out.append("widget:field_<crop> 3x2 (" + " -> ".join(CROP_STAGES) + "; a crop of: " + ", ".join(CROPS) + ")")
    return "; ".join(out)


__all__ = ["CROPS", "CROP_STAGES", "WIDGETS", "widget_of", "size_of", "widgets_told"]

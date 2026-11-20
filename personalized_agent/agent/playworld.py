"""THE PLAY VIEW'S WORLD - the agent's picture of the user's life, as a place to walk in (the user, 2026-11-17: "切换到一个
play view之后，你的agent会根据他对你的理解和看法，生成不同的场景（是你经历的事情），但是到某个位置就会有你当时的activity的图片显示
出来。比如一开始生成的一个你家，然后随着你经历越来越多，你扩展生成的空间也越来越多"; "本质上是agent根据用户经历，结合自己想象生成的
世界。这个世界是incremental的"; "agent生成的物品，也要写上agent对用户的感受，比如为什么生成这个"; "每个物品生成的日期，每个地盘扩展的
日期都要记录").

The model chooses WHAT is there - which rooms, which things in them, each with its reason and what the agent feels of
the user in it, and which of the user's pictures hangs where - from a fixed list of kinds; WHERE it goes is laid out
here, the same each time, on a grid: one room per cell, roads between, home in the middle. It only grows: rooms are
added, a room grown larger when it is full, things added - never redrawn, so a place the user knows stays as it was.
Every room, thing and growth says when it was made. The look is the page's (api/web/play.js): one tile set (Kenney's
Roguelike packs, CC0), one palette.

Kept as JSON beside the application's data (`folder`/world.json); the pictures of things - small copies of the user's
chat pictures - beside it, kept as long as the thing is (the user's say: the play view's own, apart from the eight days
a chat picture is kept). The user's data ("已经生成好的世界纪录是会作为用户数据保存的"): never tidied away.

CAUGHT UP A PART AT A TIME ("新安装的用户，会根据他已有的数据开始画（一次性画一部分，逐步catch up the most recent progress)"):
the world remembers the day it has been drawn up to; each growth draws the next STEP_DAYS of what the user lived (from
the earliest kept, at most BACK_DAYS ago) - often while behind, once a day when caught up. Each thing says when it was
made and the days it comes from.
"""

from __future__ import annotations

import copy
import json
import math
import logging
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..models.base import ModelRequest
from ..common.languages import in_language
from . import playplaces as places
from . import playregions as regions

log = logging.getLogger(__name__)

from . import playcatalogue as catalogue
from . import playgreat as great
from . import playshapes as shapes
from . import playfind
from . import playmade as made_lib
from . import playpieces as pieces
from . import playwidgets as widgets
from . import playmuse as muse
TASK = "play_world_grow"
#: The search before it: what the model wants to know, looked up in the agent's own records.
TASK_LOOK = "play_world_look"
#: Things compared by their pictures before one is chosen (agent/playcatalogue.py).
TASK_PICK = "play_world_pick"
#: Each project conceived first, in a call of its own; built after.
TASK_CONCEIVE = "play_world_conceive"
CONCEIVE_SYSTEM = """You keep a small world that pictures the user's life. Before anything is built for their projects, \
CONCEIVE each one as an artist would: what its name, its work and its feeling call up - a metaphor carried all the way \
through, so its ground is unmistakably it. Three ways to find it (the user's examples), often woven together: BY ITS \
NAME - a project called Watermelon is fruit, a melon patch, a farm; BY ITS SCENE - a project of videos on the water is \
the water itself, boats and piers; BY ITS TIME - making dumplings at the Spring Festival is the Spring Festival, red \
lanterns and a family table. WaterMelon is not a room with its name on the floor: it is a melon farm estate - the \
farmhouse its studio, the melon fields where its versions ripen, a seed vault for its releases, a stall by the road \
where its users come, a water tower for its servers.

FIRST, WHICH TASKS ARE ONE PROJECT (the user: "personal agent其实和watermelon是同一个task, 你的model要能够认出来"): \
tasks ("tasks_now", and those drawn before, "drawn_before") that are parts of one piece of work - the same product by \
two names, its building and its release, its paper and its talk - are ONE project; their grounds stand together, a \
block each. Projects kept before ("projects_so_far") keep their tasks, more joined to them as they come. A task of its \
own is a project of one.
THEN EACH BLOCK ON ITS OWN, by its own name (the user: "西瓜想到瓜田，personal agent想到机器人"), as above; one with a \
concept keeps it - grow the same idea further - unless its life has truly changed. Small tasks ("small_tasks") grouped \
into one scene are conceived as one block, under the first of them.
LAST, THE PROJECT'S CONCEPTS JOINED (the user: "最终进行summary of conception, 形成一个机器+农作物的联合imagination"): \
one imagination its blocks all belong to - a farm worked by robots, machines among the melon rows - so the blocks \
read as one estate.
Answer with only JSON: {{"projects": [{{"name": "<the project's name>", "tasks": ["<task id>", ...], "summary": "<the \
joined imagination, at most 3 sentences>", "blocks": [{{"task": "<task id>", "name": "<this block's own name>", \
"associations": ["<a word or two>", ...up to 8], "idea": "<its whole scene, at most 3 sentences>", "parts": ["<a part \
and what of the project it is, one short sentence>", ...up to 10], "fill": ["<a kind of thing that fills its open \
ground>", ...up to 8]}}]}}]}}. The kinds for "fill": {kinds}."""
#: The projects kept, and how many tasks they may be told of from before.
PROJECTS_MOST, DRAWN_BEFORE_MOST = 12, 30
#: The themes chosen first, their things given after (agent/playcatalogue.py theme_tree).
TASK_THEMES = "play_world_themes"
THEMES_MOST = 4
THEMES_SYSTEM = """You keep a small world that pictures the user's life, and are about to grow it from these days. \
Its things come in themes, the themes in groups ("THEMES": group -> theme -> what it is). Choose the themes - at most \
{most} - whose things this growth may want: what these days were, the lands the world has, a festival or a season they \
lived, a place's style. You will be given the things of those you choose. HOW OFTEN EACH WAS CHOSEN BEFORE is given \
("themes_used_before"; one not there was never chosen): where these days allow, choose those used least - never \
used best - so the world keeps finding new things (the user, 2026-11-20: "主题的选择也尽可能选没用过的主题"). Answer \
with only JSON: {{"themes": ["<theme>", ...]}}."""
PICK_SYSTEM = """You are growing a small world that pictures the user's life, and asked to see some things side by side \
before choosing. For each choice below you are shown its candidates in one picture, each with its id under it, and told \
what the thing is for. Choose the one that looks most right for it - its look, its colours, how it would sit in that \
place. Answer with only JSON: {{"picks": {{"<choice number>": "<the id you chose>"}}}}."""
LOOK_SYSTEM = """You keep a small world that pictures the user's life, and are about to grow it from these days. \
Before you do, you may look things up in what the agent itself has kept of them - its chats with them, its memory \
notes, what they read, what it saw them doing, their tasks, projects, to-dos and calendar. Ask at most {most} short \
searches (a few telling words each, in the language the records are likely in) that would let you draw these days \
truer and finer - a project's outcome, a place they went, something they kept coming back to.
Answer with only JSON: {{"look_up": ["...", "..."]}} - an empty list when you need nothing."""
#: The kinds of room, each with its floor - the model chooses among these only.
ZONE_KINDS: Dict[str, Dict[str, Any]] = {
    "home": {"floor": "wood"}, "study": {"floor": "stone"}, "library": {"floor": "wood"},
    "screening": {"floor": "stone"}, "cafe": {"floor": "tiles"}, "studio": {"floor": "wood"},
    "station": {"floor": "tiles"}, "garden": {"floor": "grass", "outdoor": True},
    # A LINE'S STATION, ITS OWN GROUND (2026-11-20: "还要有大的火车站，地铁站，飞机场之类。（好几格的那种）"): open ground the
    # station - a great piece - stands on (_stations).
    "terminal": {"floor": "paving", "outdoor": True},
    # A CITY'S OWN BUILDINGS (2026-11-20: "商场，公寓，摩天大楼，医院，警察局，消防局"): the ground each stands on (_civic).
    "civic": {"floor": "paving", "outdoor": True},
    # Open places (agent/playplaces.py): a park, a pool, a courtyard, a terrace.
    "park": {"floor": "grass", "outdoor": True}, "pool": {"floor": "paving", "outdoor": True},
    "courtyard": {"floor": "cobble", "outdoor": True}, "terrace": {"floor": "bridge", "outdoor": True},
    # Nature's (agent/playplaces.py NATURAL): a lake with an islet, a waterfall, a fountain plaza, a grove, a meadow.
    "lake": {"floor": "grass", "outdoor": True}, "waterfall": {"floor": "rock", "outdoor": True},
    "plaza": {"floor": "paving", "outdoor": True}, "grove": {"floor": "grass2", "outdoor": True},
    "meadow": {"floor": "grass", "outdoor": True},
    # Heights (2026-11-17: "雪山，火山口，高峰"): a snowy mountain, a volcano's crater, a high peak.
    "mountain": {"floor": "grass", "outdoor": True}, "volcano": {"floor": "rock", "outdoor": True},
    "peak": {"floor": "grass", "outdoor": True},
    # Fun and wonder (2026-11-17: "水族馆主题"; "海底隧道，过山车，游乐场主题"; "衰败墓园"; "植物园"): an aquarium, a glass way
    # through the sea, a roller coaster, a fairground, a cemetery, a greenhouse.
    "aquarium": {"floor": "tiles"}, "greenhouse": {"floor": "grass"},
    # Where people work, learn and live (2026-11-17: "学校教学楼，实验室，办公场所，公寓里等").
    "classroom": {"floor": "wood"}, "lab": {"floor": "tiles"}, "office": {"floor": "tiles"}, "apartment": {"floor": "wood"},
    "lecture_hall": {"floor": "wood"},
    "sea_tunnel": {"floor": "water", "outdoor": True}, "roller_coaster": {"floor": "grass", "outdoor": True},
    "funfair": {"floor": "paving", "outdoor": True}, "cemetery": {"floor": "grass", "outdoor": True},
    # A PROJECT'S GROUND (agent/playworld.py PlayWorld._plots): as large as its time, its places and its scenes in it.
    "estate": {"floor": "grass", "outdoor": True},
}
#: A PROJECT'S PLOT AS LARGE AS ITS TIME: its tiles PLOT_TILES * hours ** PLOT_POWER (an hour about 140, ten about 790,
#: fifty about 2,600, 160 about 6,300); wider than a third of a region it is a DISTRICT - a region of its own.
PLOT_TILES, PLOT_POWER, PLOT_LEAST, DISTRICT_PART = 140, 0.75, 12, 0.6
#: How thickly a plot's open ground is filled with its scenes: a thing for each this many free tiles, at most
#: PLOT_FILL_MOST at a time.
PLOT_FILL_TILES, PLOT_FILL_MOST = 7, 120
#: Arrangements (many of one thing, one design) applied in a growth, and the most of one.
MOST_ARRANGED, ARRANGED_MOST = 8, 60
PATTERNS = ("rows", "grid", "line", "ring", "border", "cluster", "picture")
#: A picture laid in things: its most rows and letters a row, and the most tiles it lays.
PICTURE_SIDE, PICTURE_MOST = 40, 900
#: The days kept of each task (its ties); the days two must share for a line to run between their lands; the most
#: lines; how many region cells a district may spread to, across and down.
DAYS_KEPT, COMMUTE_DAYS, COMMUTES_MOST, SPREAD_MOST = 400, 5, 3, 3
#: Places only the code makes - never offered to the model as a kind (a line's station ground, _stations).
CODE_KINDS = ("terminal", "civic")
#: The most stops on one place's way through (PlayWorld.routes).
ROUTE_STOPS_MOST = 40
#: How many of the stretch's tasks the feeling's call is told, to name the fields of their work (_mood).
MOOD_WORK_TOLD = 12
#: How much ground a great ground has round its places' floors, when it is laid on free ground (_great_free).
GREAT_ROOM_ROUND = 2.2
#: What a land opened for a place grown too large keeps of the land it began in (_district).
DISTRICT_THEME = ("scene", "weather", "look", "edges", "animals", "road", "music", "mood", "feeling", "to_them")
#: How tied two lands must be (their time on the same days over the larger of their times) to be commuted between;
#: the least tie a task split out of another has with its parent's land; how much larger a ground must grow before
#: its inside is laid again.
COMMUTE_TIE, SPLIT_TIE, RELAY_INSIDE = 0.15, 0.5, 1.4
#: How many tiles above a district's main place are kept for its landmark (api/web/play.js playLandmarkDraw rises
#: about that far).
LANDMARK_ROOM = 12


def plot_size(minutes: float) -> Tuple[int, int]:
    """The plot of a project that took `minutes`: wider than tall, as the land is."""
    import math
    area = PLOT_TILES * max(1.0, minutes / 60) ** PLOT_POWER
    w = max(PLOT_LEAST, int(round(math.sqrt(area * 1.3))))
    return w, max(PLOT_LEAST, int(round(area / w)))


#: How many choices are compared by their pictures in one growth.
MOST_COMPARED = 6
#: The kinds of thing: its size in tiles, and whether it hangs on a wall. "frame" holds one of the user's pictures.
ITEM_KINDS: Dict[str, Dict[str, Any]] = {
    "frame": {"size": (2, 2), "wall": True}, "window": {"size": (1, 1), "wall": True},
    "mirror": {"size": (1, 2), "wall": True},
    "bookshelf": {"size": (1, 2)}, "bookcase": {"size": (2, 2)}, "desk": {"size": (1, 1)}, "table": {"size": (2, 1)},
    "chair": {"size": (1, 1)}, "bed": {"size": (2, 2)}, "wardrobe": {"size": (1, 2)}, "cabinet": {"size": (1, 2)},
    "lamp": {"size": (1, 2)}, "piano": {"size": (2, 1)}, "plant": {"size": (1, 1)}, "kitchen": {"size": (4, 1)},
    "stove": {"size": (1, 1)}, "flowerpot": {"size": (1, 1)}, "tree": {"size": (1, 1)}, "pine": {"size": (1, 1)},
    "bush": {"size": (1, 1)}, "fireplace": {"size": (1, 1)},
    # Finer things (2026-11-17): to sit on, to eat at, to light, to cook with, to play, to keep in; and on the walls.
    "sofa": {"size": (2, 1)}, "armchair": {"size": (1, 1)}, "dining_table": {"size": (2, 2)},
    "candelabra": {"size": (1, 1)}, "candle": {"size": (1, 1)}, "sink": {"size": (1, 1)}, "counter": {"size": (1, 1)},
    "organ": {"size": (1, 2)}, "barrel": {"size": (1, 1)}, "rug": {"size": (3, 2), "under": True},
    "paint": {"size": (1, 1), "under": True},    # a tile of a picture laid in things, painted on the ground
    "wall_torch": {"size": (1, 1), "wall": True}, "painting": {"size": (1, 1), "wall": True},
    "map": {"size": (1, 1), "wall": True}, "tapestry": {"size": (3, 1), "wall": True},
    "portrait": {"size": (1, 2), "wall": True}, "wall_shelf": {"size": (1, 1), "wall": True},
    "shield": {"size": (1, 1), "wall": True},
    # Outdoors: a fountain, a lounger by the pool, a parasol, a statue, a lamp post, a hedge, a flowerbed.
    "fountain": {"size": (3, 3)}, "lounger": {"size": (1, 1)}, "parasol": {"size": (1, 1), "under": True},
    "statue": {"size": (1, 2)}, "lamp_post": {"size": (1, 1)}, "hedge": {"size": (1, 1)},
    "flowerbed": {"size": (1, 1), "under": True},
    # Kenney's Tiny Town and Tiny Dungeon (CC0): a chest, a well, a beehive.
    "chest": {"size": (1, 1)}, "well": {"size": (1, 2)}, "beehive": {"size": (1, 1)},
    # CUSTOMIZED (the user, 2026-11-17: "有customized东西"): one of the task's pictures as a statue on a pedestal, a
    # banner, a poster; or a thing the agent draws itself, pixel by pixel.
    "statue_of": {"size": (1, 2), "picture": True}, "banner": {"size": (1, 2), "wall": True, "picture": True},
    "poster": {"size": (2, 2), "wall": True, "picture": True},
    "pixel_art": {"size": (1, 1), "pixels": (16, 16)}, "pixel_art_large": {"size": (2, 2), "pixels": (32, 32)},
    # A GREAT THING IN FINE GRAIN (the user, 2026-11-18: "你现在这种水族馆设计也太粗粒度了，对这种大的东西，一定要细粒度"): drawn
    # by the agent at the world's own grain - sixteen of its pixels a tile - never a small thing made large.
    "pixel_art_huge": {"size": (4, 4), "pixels": (64, 64)}, "pixel_art_wide": {"size": (6, 3), "pixels": (96, 48)},
    "wall_art": {"size": (2, 1), "wall": True, "pixels": (32, 16)},
    # Living things (Tiny Creatures, CC0): a pet, a robot.
    "cat": {"size": (1, 1)}, "dog": {"size": (1, 1)}, "rabbit": {"size": (1, 1)}, "robot": {"size": (1, 1)},
    # Festivals, fairs, the sea, a graveyard, a garden's (2026-11-17).
    "gravestone": {"size": (1, 1)}, "coffin": {"size": (2, 2)}, "pumpkin": {"size": (1, 1)},
    # MADE TO HANG (drawn by the page, play.js PLAY_HANGINGS): a room's ceiling has more than lanterns.
    "chandelier": {"size": (2, 1), "hangs": True}, "hanging_plant": {"size": (1, 1), "hangs": True},
    "wind_chime": {"size": (1, 1), "hangs": True}, "birdcage": {"size": (1, 1), "hangs": True},
    "mobile": {"size": (1, 1), "hangs": True}, "hanging_cloth": {"size": (1, 2), "hangs": True},
    "crystal_chandelier": {"size": (2, 2), "hangs": True}, "pumpkin_lantern": {"size": (1, 1), "hangs": True},
    "lantern": {"size": (1, 1)}, "firecrackers": {"size": (1, 1)}, "heart": {"size": (1, 1)}, "roses": {"size": (1, 1)},
    "balloons": {"size": (1, 1)}, "present": {"size": (1, 1)}, "xmas_tree": {"size": (1, 1)}, "fish_tank": {"size": (2, 1)},
    "jellyfish": {"size": (1, 1)}, "coral": {"size": (1, 1)}, "ticket_booth": {"size": (2, 2)}, "candy_stand": {"size": (2, 2)},
    "ferris_wheel": {"size": (5, 5)}, "carousel": {"size": (4, 4)}, "coaster_car": {"size": (1, 1)},
    "sunflower": {"size": (1, 1)}, "crops": {"size": (1, 1)},
    "whiteboard": {"size": (2, 1), "wall": True}, "school_desk": {"size": (1, 1)}, "lab_bench": {"size": (2, 1)},
    "microscope": {"size": (1, 1)}, "computer": {"size": (1, 1)}, "cubicle": {"size": (2, 2)},
    "water_cooler": {"size": (1, 2)}, "printer": {"size": (1, 1)}, "lockers": {"size": (2, 2)}, "bunk_bed": {"size": (2, 2)},
    "kitchenette": {"size": (2, 1)}, "server_rack": {"size": (1, 2)},
}
# EACH THEME ITS OWN THINGS TO HANG (the user, 2026-11-19: "那个素材可以每个主题都做一个expansion, 不同主题的吊灯不一样，让model
# 选"): api/web/play/hangings.json - each a kind the room design may hang, drawn by the page from its shape and colours.
THEME_HANGINGS: Dict[str, List[str]] = {}
try:
    _hangings = json.loads((Path(__file__).resolve().parent.parent / "api" / "web" / "play" / "hangings.json")
                           .read_text(encoding="utf-8")).get("hangings") or {}
except (OSError, ValueError):
    _hangings = {}
for _theme, _ones in _hangings.items():
    for _name, _shape, *_rest in _ones:
        if _name not in ITEM_KINDS:
            ITEM_KINDS[_name] = {"size": (1, 2) if _shape in ("lantern", "banner", "net") else (1, 1), "hangs": True,
                                 "theme": _theme}
            THEME_HANGINGS.setdefault(_theme, []).append(_name)
# A PROGRESS WIDGET (agent/playwidgets.py): a kind of its own, its size each widget's.
ITEM_KINDS.setdefault("widget", {"size": (2, 2)})
ITEM_KINDS.setdefault("piece", {"size": (3, 3)})          # a great piece (agent/playpieces.py): its size its own
# EVERY THING IN THE CATALOGUE A KIND TOO (agent/playcatalogue.py): its size its picture's.
for _name in catalogue.catalogue()["things"]:
    if _name not in ITEM_KINDS:
        ITEM_KINDS[_name] = {"size": catalogue.size_of(_name) or (1, 1), "thing": True}
#: A place's own style when it is made, by kind (agent/playplaces.py STYLES).
FIRST_STYLE = {"home": "cottage", "study": "classic", "library": "classic", "screening": "modern", "cafe": "rustic",
               "studio": "industrial", "station": "modern"}
#: At most, from one growth: places enlarged, and redesigned.
MOST_ENLARGED, MOST_REDESIGNED = 2, 1
#: Merges in one growth: places gathered into a town, or into one great house.
MOST_MERGED = 1
#: A cell of the grid: a room in it, a road along its bottom and its right.
CELL_W, CELL_H = 18, 15
#: A room's floor, in tiles: as it is made, and at most (its walls round it).
ROOM_FIRST, ROOM_MOST = (11, 6), (14, 8)
#: At most, from one growth.
MOST_NEW_ZONES, MOST_NEW_ITEMS = 6, 40
#: A door's width, in tiles (the user, 2026-11-17: "你现在房间的门都太小了，弄大点") - api/web/play-regions.js the same.
DOOR_W = 2
#: A thing's picture, no larger than this across.
#: A picture's copy kept with its thing: sharp when looked at (2026-11-19: was 320; its own name - it had taken
#: PICTURE_SIDE's, the most rows of a picture laid in things, from 40 to 320).
PICTURE_COPY_SIDE = 1024
#: How often the day's growth is looked for (and while catching up); and the chat pictures offered, at most.
LOOK_SECONDS, BEHIND_SECONDS, MOST_OFFERED = 1800, 300, 12
#: Looked at this soon after the agent starts; the next part drawn this soon after one, while behind (`_watch`).
START_SECONDS, CHAIN_SECONDS = 15, 3
#: Parts drawn one after another at most, when the world is grown at once (grow_soon) - against a runaway.
CHAIN_PARTS_MOST = 400
#: Each growth draws this many days of what was lived; the earliest drawn is at most this long ago.
STEP_DAYS, BACK_DAYS = 14, 180

SYSTEM = """You are the artist of a small world that is your picture of the user's life - a world they walk in, \
made of lands, places (zones) and the things in them. You are making a work of art, not filling in a form: each \
growth you take what they lived and give it back as places that feel like those days.

WHAT YOU MAKE IT FROM
- EACH PROJECT'S MUSES ("muses": a poem, a piece of music, a painting and a video, found or made for it, and what \
the four ask of its places) - design from them: take the painting's colours, the poem's images, the music's pace, \
the video's scene, and let them become walls, floors, light and things. Someone who knows the poem should feel it \
walking in.
- EACH PROJECT'S CONCEPT ("concepts": its idea, conceived before this - its associations, its whole scene, its \
parts) - carried all the way through, one imagination for all its blocks.
- ITS WORK AS A TREE ("work_tree" of an important task: its cards - the goal, the deliverables they would \
recognise, their parts - each with its depth and status): the shape of its ground. A deliverable with parts gets a \
place of its own inside the task's ground (a zone with "part_of" and "card": its id); every card is shown by at \
least one thing ("card": its id on the thing) - what it is, not its name; done, finished; working, half made; open, \
waiting; set aside, put by. What you leave out is still given a thing.
- WHAT THEY LIVED ("what_they_lived_these_days", "important_tasks" with their materials, pictures and story, \
"small_tasks", "looked_up", "who_they_are_now") and THEIR TASTES ("their_tastes": what they like and dislike - \
choose styles and colours they would love).
Think as a set designer of the warm, crowded worlds of the old 2D adventure games: a place is a moment, full and \
layered, with a few things the eye goes to and ways to walk between - never a bare room, never things scattered.

WHAT YOU MAY ADD (only add: never remove or move what is there; at most {zones} zones and {items} things)
- ONLY THEIR HOME YET ("only_home": its zone id): the world outside is not made until their feelings are known - make \
no place and no land; everything you add goes in their home, room by room, as warm and full of them as you can.
- "content_switch": {{"switched": true or false, "what": "..."}} - first: true only when these days show a part of \
their life the world does not hold yet. Not switched: no new zone (except an important task's own place, below) - \
make the places there richer instead.
- PLACES, "zones": [{{"kind", "name", "why", "music", ...}}]. Buildings {indoor_kinds}; open places {outdoor_kinds}. \
A PLACE OF ONE TASK'S WORK SAYS WHICH ("about": "<task id>") - its rooms of a project's work belong to it, \
whatever they are called. An important task without a place ("its_place" empty) gets one: "task": "<task id>", named as the task is (a \
product's own name kept exactly), with "grounds": an open kind and "grounds_name" when a yard, garden, pool or park \
suits it. Its ground ("plot", sized by its hours) is ONE estate: more of its places stand inside it with "part_of": \
"<task id>" - never a place for it outside. Other ways to a new place: "between": [zone ids] - a place of the idea \
two neighbouring projects make together ("neighbourhoods"); "goes_on": [A's id, B's id] - where A then B lead next \
("lines"); "tasks": [ids] - small tasks alike, one whole scene (a park of their corners, a street of their stalls); \
"near": "<zone id>" - by the place most like it; "apart": "island" or "cave" - only for what truly stands alone. \
A building's "form": one of {forms} - HOW OPEN IT IS IS YOURS TO CHOOSE, from what the place is and how it is used, \
none favoured: shut all round with its door (a study, a vault, a workshop at night), open in front (a hall, a stall), \
wings round a court, a pavilion open on every side. ITS OUTLINE ("shape", any place, open or under a roof - \
the user, 2026-11-19: "每个区域都可以选其他形状，比如五角星，三角形，圆形等"): one of {outlines}, or none for a plain \
rectangle - its floor and its walls follow it. "sign": one of {signs}. THE WAY TO IT IS DESIGNED TOO \
("road": {{"material": one of {roads}, "width": 1 to 3, "curve": 0 straight to 1 winding}}): from what the task is \
at its heart and how it sits in the land's scene - a grand avenue to a great work, a winding footpath to a quiet one. Nature is a place too - a lake with an \
islet, a waterfall, a grove - where the land and their life would have them. Leisure gets a place as work does.
- THINGS, "items": [{{"zone": "<id or a new zone's name>", "kind", "name", "why", "from", "picture"}}] - kinds \
{item_kinds}, and any thing of "THINGS_BY_THEME" ("id: what it is (size)"); unsure which looks right, give \
"compare": [two to four ids] in place of "kind" and you will be shown them side by side. Things come in small scenes \
(a desk with its chair, its lamp, its books and a plant); an important task gets about its "things_to_add" \nthis time. From the task's materials: a document a bookshelf, a page a \
map, a message a lamp by the desk - "from" naming it. ON THE MAP CARTOON THINGS ONLY, NEVER A SCREENSHOT (the user, \
2026-11-19: "这个地图上应该画的是卡通物品，然后交互细节里有图片（比如申请表应该是个相关的物品）"): what it is shown by the \
thing that is it - an application form a clipboard with a form, a paper a stack of pages, a release a crate; a \
frame, a poster, a banner or a statue is drawn as one. Their pictures go IN ITS CARD: "picture": one of its PICTURES \
ids. ANY THING MAY ALSO ASK FOR A PICTURE IN ITS CARD - \
"find_picture": "<THE THING ITSELF, as exactly as you can say it - their own work said to be theirs and named as \
its card names it; a public thing by its public name; never an example or a template of something like it>" - \
LOOKED FOR in their own files, screenshots and pages for what is theirs, on the web for what is public (the user: \
"模型主要rely on 自己搜"; "这个材料包的照片明显不是我的"); the thing drawn as itself, the picture kept in its card - \
WHEREVER YOU THINK A PICTURE FITS (the user: "觉得可以配图的就配图"). The \
project's own: a "statue_of", "banner" or "poster"; or one you draw - "pixel_art" (16x16), \
"pixel_art_large" (32x32), "wall_art" (32x16, on a wall), "pixel_art_huge" (4 x 4 tiles, 64x64), "pixel_art_wide" \
(6 x 3 tiles, 96x48) with "pixels": {{"palette": ["#rrggbb", ...up to 10], "rows": ["<a digit per pixel, . for \
none>", ...]}} - a large thing drawn in fine grain, each fish of an aquarium, each vine of a field.
- ARRANGEMENTS, "arrange": [{{"task": "<task id - its plot>" or "zone": "<zone id>", "kind", "count": 1 to 60, \
"pattern": rows, grid, line, ring, border, cluster or picture, "where": north, south, east, west, middle or \
"around:<zone id>", "scale": 1 to 6, "principle": "contrast" or "consistency", "name", "why"}}] - many of one thing \
as ONE design with ONE reason (melons in rows across a field, lanterns along a path); "scale" makes only one of their \
pictures larger, and only on open ground. "pattern": "picture" lays a great picture in things, seen from above - \
"picture": {{"rows": ["<up to 40 letters a row, up to 40 rows, . for nothing>"], "legend": {{"<letter>": "<a kind>" \
or "#rrggbb"}}}} - a giant watermelon in melon plants and red paint, for a project's great ground.
- ITS STORY, "story": [{{"task": "<task id>", "step": "<its step>", "name": "<a few words: the first demo, the \
sidebar arrives>", "why": "<what it was then and how it had grown>", "picture": "<one of that step's picture ids, or \
none>"}}] - each step of an important task's story not yet told, in order, so walking through they see their work \
grow.
- A PLACE'S LOOK, "decorate": [{{"zone", "on": "wall" or "floor", "floor_color": "#rrggbb", "floor_picture": "<a \
picture id>", "mural": {{"palette": [...up to 8], "rows": ["<up to 24 digits 0-7 or .>", ...up to 16]}}, \
"floor_text": "<only when the name is the look, up to 12 letters>"}}] - its logo, a picture you draw, its colour on \
its walls; not every place needs one.
- GROWING: "enlarge": [{{"zone", "why"}}] (at most {enlarged}); "redesign": [{{"zone", "style": one of {styles}, \
"scale": one of {scales}, "why"}}] (at most {redesigned}) - for a place marked "full" or grown past itself; "merge": \
[{{"into": "town" or "house", "name", "zones": [ids], "why", "style"}}] - rarely, when what they share has grown.
- A NEW LAND, when "a_new_region" is given ("feeling": these days feel unlike the land's - open it; "leave": lead \
them out of the Starting Village; "change": a new chapter of their life; "full": no room left): "region": {{"name", \
"why", "biome", "layout", "palette", "architecture", "theme", "landmark", "weather", "lights", "music", "catch", \
"travel", "road"}} - each one of its list: biome {biomes}; layout {layouts}; palette {palettes}; architecture \
{architectures}; theme {themes}; landmark {landmarks}; lights {lights}; weather {weathers} (a feeling's land keeps \
its scene's biome, light and weather whatever you say); "travel": true when these days they were away; "catch": 2 to \
6 of {catchable} - what its waters give (a new "lake" or "waterfall" may say its own).
- SOUND: a new zone and a new land have their own little tune - "music": {{"track": one of {tracks} (a recorded \
piece, only where one truly fits and few places have it), or your own: "tempo": 50-160, "instrument": one of \
{instruments}, "accompaniment": one of {instruments}, "style": one of {arrangements}, "mode": major or minor, \
"melody": "<8-32 eighth notes, C5 F#4 Bb5 or - for a rest>", "bass": "<4-8 notes, one a bar>"}}. THE MUSIC AS THE \
PLACE AND THESE DAYS FEEL (the user: "选择的时候要选和场景/心情align的"): read each track's pace and loudness - a tea \
house, a library, a bedroom, a garden at rest, a tired or quiet stretch take a slow, soft one or a slow tune of your \
own (tempo under 80, a lullaby, pad, ballad or solo); a workshop, a market, a festival, a driven stretch a busy one. \
Every other sound comes from a thing that makes it (a fireplace crackles).

KEEP IN MIND
- Words: every "why" is at most 3 sentences, in your own voice to them - why this, what of their days it holds, \
what you feel of them in it; warm, honest, specific, never flattery. A name goes on a sign: at most 24 characters \
(in Chinese 10), never cut off. Nothing that would embarrass them if a friend walked through.
- As rich as their life: a varied fortnight is 3 to 6 new places and 20 to 40 things; the more complex a project, \
the more it holds. A place marked "sparse" first; a great work's places less than half free ("free").
- One principle to each design: "contrast" (colours that differ, side by side) or "consistency" (one kind, one \
family of colours) - never both at once.
- Each picture of theirs once; never pictures alike (the same terminal again) - a plain screenshot a frame at most.
- Zone kinds: {zone_kinds}.

Answer with only JSON:
{{"content_switch": {{...}}, "zones": [...], "items": [...], "arrange": [...], "story": [...], "decorate": [...], \
"enlarge": [...], "redesign": [...], "merge": [...], "region": {{...}} (only when you open one)}}
Empty lists when nothing new is worth adding.
THEIR TASTES are given as "their_tastes"."""


#: A ROOM'S SOUND (the user, 2026-11-17: "不同场景可以是不同音效（这个音效可以ai 生成吗，不能的话ai根据场景自己选吧)"): the
#: agent writes each new room's tune as notes - played by the page's own synthesis (api/web/play-sound.js) - and
#: chooses the sound around it from these. A room without either plays its kind's own.
AMBIENCES = ("none", "birds", "rain", "wind", "crowd", "fire", "water")
INSTRUMENTS = ("piano", "strings", "flute", "harp", "guitar", "musicbox", "marimba", "organ", "celesta", "pad",
               # MORE TO CHOOSE FROM (the user, 2026-11-19: "你的音乐可选再加多") - play-sound.js PLAY_INSTRUMENTS the same
               "bells", "kalimba", "vibraphone", "clarinet", "accordion", "koto", "choir", "chiptune", "steeldrum", "ocarina", "bass",
               "violin", "cello")      # a bowed string alone (the user: "你怎么没有弦乐，小提琴那种")
#: Recorded tracks a place may have (api/web/play/music, CC0), EACH AS IT REALLY SOUNDS (the user, 2026-11-20: "场景是
#: 等候茶馆，但是音乐很紧凑。你的每个音乐要加一个描述，选择的时候要选和场景/心情align的"): measured from the audio
#: (scratchpad tool, 2026-11-20: notes struck a second, loudness, brightness, how it swells) and heard against its
#: name - several were described from their titles only before ("calm-loop" is bright and dense, "just-saying-tho" a
#: loud, tight beat). PACE: slow under 2 notes a second, steady 2-3, busy over 3; LOUD: soft under -17 dB, medium,
#: strong over -12 dB.
TRACKS = {
    "home": "warm, homely, gentle - steady pace, soft; an evening at home",
    "friends": "light, friendly, sociable - slow, soft, bright; friends together, unhurried",
    "peaceful": "calm, deep and still, slowly swelling - slow, medium, dark and low; water at rest",
    "sand": "wistful, by the sea - slow, soft, minor; a quiet shore",
    "summer": "bright, outdoors, carefree - slow, medium; a light summer day",
    "innocence": "tender, thoughtful, night - very slow, very soft; alone, late, reflective",
    "town": "a gentle town in a minor key - slow, medium; old streets, a little melancholy",
    "piano": "solo piano, studious, minor - steady, very soft; reading, thinking",
    "piano-calm": "quiet piano, deep focus, dark - steady, soft; long concentrated work",
    "frogtown": "a quiet, even little town - slow, medium, never changing; everyday life",
    "calm-loop": "a bright, dense, bubbly short loop - busy, strong; NOT calm despite its name: a lively shop or play",
    "the-hope": "orchestra with violins, hopeful, building slowly - slow, soft, swelling far; a new beginning, a waiting hope",
    "snow-fest": "winter festival, bright and bustling - busy, strong; a celebration in the snow",
    "brand-new-wisdom": "a quick, fresh, punchy short loop - busy, strong; energy, a start",
    "just-saying-tho": "a tight, loud, driving beat loop - busy, strong, unchanging; a workshop or street in a hurry - "
                       "never a calm place",
    "winter-dust": "a soft, glittering wintry loop - busy but very soft; snowflakes, a quiet winter play",
    "swinging-sweet": "a sweet, swinging, punchy loop - busy, strong; a playful dance",
    "treasure-hunter": "orchestral with violin, adventurous, quick - busy, medium-strong; a quest, a chase",
    "magic-puzzle": "strings and violin, magical and puzzling, minor - steady, strong, even; a mystery to solve",
    "busy-calm-street": "a chill city street, relaxed though busy round it - slow, medium, swelling; a city walk",
}
#: Each track's measures, for the page's choosing and its tests: notes a second, loudness in dB.
TRACK_MEASURES = {
    "home": (2.71, -16.0), "friends": (1.66, -18.2), "peaceful": (1.70, -13.0), "sand": (1.88, -18.0),
    "summer": (1.62, -14.1), "innocence": (1.10, -24.6), "town": (1.45, -13.1), "piano": (2.25, -24.2),
    "piano-calm": (2.53, -18.6), "frogtown": (1.67, -15.8), "calm-loop": (4.13, -9.5), "the-hope": (1.76, -18.3),
    "snow-fest": (4.19, -10.8), "brand-new-wisdom": (3.31, -11.6), "just-saying-tho": (3.83, -8.5),
    "winter-dust": (3.92, -20.8), "swinging-sweet": (4.05, -8.6), "treasure-hunter": (3.90, -12.5),
    "magic-puzzle": (2.11, -11.6), "busy-calm-street": (1.50, -14.6),
}
#: How a tune is arranged under its melody (api/web/play-sound.js).
ARRANGEMENTS = ("solo", "arpeggio", "pad", "waltz", "march", "lullaby", "bossa", "ballad", "bounce", "folk", "tango", "ambient", "stride")
_NOTE = re.compile(r"^[A-G][#b]?[1-7]$")


#: WHAT ITS WATERS GIVE (the user, 2026-11-18: "不同位置的鱼塘可能钓出来的东西是不一样的，这个也是你的设计的一部分"):
#: what a land or a water place may say is fished from it - drawn by the page (play-act.js, PLAY_SCENERY).
CATCHABLE = ("clownfish", "blueTang", "yellowTang", "moorishIdol", "butterflyfish2", "parrotfish", "wrasse", "triggerfish",
             "boxfish", "redSnapper", "grouper", "koi", "fish1", "smallFish", "angelfish", "jellyfish", "shark", "seaweed",
             "coral", "anemone", "goldIngot", "runeStone", "graveCandle", "jackOLantern", "pumpkin", "present",
             "redEnvelope", "loveLetter", "redHeart", "lovePotion", "metalBot5", "turnipRoot", "tomatoFruit", "cherries",
             "sparkle", "wisp", "balloons", "scroll")
WATER_KINDS = ("lake", "waterfall")


def indoors(zone: Dict[str, Any]) -> bool:
    """A room under a roof: not a project's open ground, not an open place."""
    return not zone.get("plot") and not ZONE_KINDS.get(str(zone.get("kind") or ""), {}).get("outdoor")


def catch_of(said: Any) -> List[str]:
    """What the model said is fished there: names it may use, each once, six at most."""
    names = [str(one) for one in (said if isinstance(said, list) else []) if str(one) in CATCHABLE]
    return list(dict.fromkeys(names))[:6]


def tune_of(said: Any) -> Optional[Dict[str, Any]]:
    """A tune as the model wrote it, checked: notes by name or "-", an instrument of the few, a tempo in reason. None
    when too little of it is a tune."""
    if not isinstance(said, dict):
        return None

    def notes(text: Any, most: int) -> List[str]:
        return [one for one in str(text or "").split() if one == "-" or _NOTE.match(one)][:most]
    melody = notes(said.get("melody"), 32)
    if sum(1 for one in melody if one != "-") < 4:
        track = str(said.get("track") or "")
        return {"track": track} if track in TRACKS else None
    try:
        tempo = max(50, min(160, int(float(said.get("tempo") or 90))))
    except (TypeError, ValueError):
        tempo = 90
    instrument = str(said.get("instrument") or "")
    accompaniment = str(said.get("accompaniment") or "")
    style = str(said.get("style") or "")
    mode = str(said.get("mode") or "")
    track = str(said.get("track") or "")
    return {**({"track": track} if track in TRACKS else {}), "tempo": tempo,
            "instrument": instrument if instrument in INSTRUMENTS else "piano",
            "accompaniment": accompaniment if accompaniment in INSTRUMENTS else "harp",
            "style": style if style in ARRANGEMENTS else "arpeggio", "mode": mode if mode in ("major", "minor") else "major",
            "melody": " ".join(melody), "bass": " ".join(notes(said.get("bass"), 16))}


#: SCREENS NEVER HUNG IN THE WORLD (2026-11-20: a Gmail inbox, a LinkedIn feed and a job application were put in
#: things' cards as "their pictures"): mail, social sites and messengers, job applications, banking, accounts and
#: sign-ins - by what the picture was read to show.
PRIVATE_SCREEN = re.compile(
    # Only these kinds - kept narrow (2026-11-20): their own app's chat, a "feed" of their work, a paper on computer
    # vision (CV) are not private.
    r"\b(?:inbox|gmail|outlook mail|mail client|linkedin|facebook|instagram|twitter|tiktok|weibo|whatsapp|telegram|"
    r"wechat|messenger|imessage|direct messages?|job applications?|greenhouse|workday|lever\.co|online banking|"
    r"bank account|credit card|password|sign[- ]in page|login page|account settings)\b"
    r"|收件箱|邮箱|微信|私信|领英|微博|求职|网银|银行账户|信用卡|密码|登录页",
    re.IGNORECASE)


def private_screen(picture: Dict[str, Any]) -> bool:
    """Whether a picture of theirs shows something private (PRIVATE_SCREEN) - never offered to the world."""
    said = " ".join(str(picture.get(key) or "") for key in ("what", "title", "app", "window"))
    return bool(PRIVATE_SCREEN.search(said))


def new_zone_id(world: Dict[str, Any]) -> str:
    """A NEW PLACE'S ID, NEVER ONE USED BEFORE (2026-11-20: made as "how many places there are, plus one", it came round
    again once a place was let go - a project's old grounds when its great ground was laid - and two places shared it):
    one past the highest ever given, kept in the world ("zone_top")."""
    numbers = [int(str(one.get("id"))[1:]) for one in world.get("zones") or [] if re.fullmatch(r"z\d+", str(one.get("id")))]
    top = max([int(world.get("zone_top") or 0)] + numbers) + 1
    world["zone_top"] = top
    return f"z{top}"


def new_item_id(world: Dict[str, Any]) -> str:
    """A NEW THING'S ID, NEVER ONE USED BEFORE (2026-11-20: ids made as "how many things there are, plus one" came round
    again once things were let go - two things under one id, a picture tried for one counted on the other, one
    screenshot file for both): one past the highest id ever given, kept in the world ("item_top")."""
    numbers = [int(str(one.get("id"))[1:]) for one in world.get("items") or [] if re.fullmatch(r"i\d+", str(one.get("id")))]
    top = max([int(world.get("item_top") or 0)] + numbers) + 1
    world["item_top"] = top
    return f"i{top}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cells(count: int) -> List[Tuple[int, int]]:
    """The first `count` cells of the grid, nearest home first - a ring at a time: home's row first, right before
    left, below before above."""
    out: List[Tuple[int, int]] = [(0, 0)]
    ring = 1
    while len(out) < count:
        around = [(x, y) for y in range(-ring, ring + 1) for x in range(-ring, ring + 1)
                  if max(abs(x), abs(y)) == ring]
        around.sort(key=lambda one: (abs(one[1]), abs(one[0]), one[1] < 0, one[0] < 0))   # right, then below, first
        out.extend(around)
        ring += 1
    return out[:count]


class PlayWorld:
    """The world, kept; grown once a day from what the user lived (and on asking)."""

    #: A new world begins as a house alone (its first land closed, _open_outside) - a test of what grows in a world
    #: already open sets this False.
    START_CLOSED = True

    def __init__(self, folder: Path, models: Any, config: Any, context: Optional[Callable[[], str]] = None,
                 pictures: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 history: Optional[Callable[[str, str], str]] = None,
                 earliest: Optional[Callable[[], str]] = None,
                 look: Optional[Callable[[List[str], str], str]] = None,
                 tastes: Optional[Callable[[], str]] = None,
                 tasks: Optional[Callable[[str, str], List[Dict[str, Any]]]] = None,
                 lineage: Optional[Callable[[], Dict[str, Dict[str, Any]]]] = None,
                 feelings: Optional[Callable[[str, str], Dict[str, List[str]]]] = None,
                 cards: Optional[Callable[[List[str]], Dict[str, List[Dict[str, Any]]]]] = None,
                 files: Optional[Callable[[str], List[Dict[str, Any]]]] = None) -> None:
        self.folder = Path(folder)
        # Their files on their computer now, by words (`files(words)` - the local index), for things' pictures.
        self.files = files
        self.models, self.config = models, config
        self.context, self.pictures = context, pictures
        # What was lived between two times (`history(since, until)`), and when the first of it was (`earliest()`).
        self.history, self.earliest = history, earliest
        # Its own records searched for what the model asks before it draws (`look(questions, until)`).
        self.look = look
        # What the agent has learned the user likes and dislikes - their style chosen by it (`tastes()`).
        self.tastes = tastes
        # The important tasks of the days drawn, with their materials (`tasks(since, until)`).
        self.tasks = tasks
        # What each task was born of - joined into another, merged from some, split from one (`lineage()`).
        self.lineage = lineage
        # What the feeling of these days is read from - their words in their chats, the rhythm of their days
        # (`feelings(since, until)`, playmuse.feelings_between).
        self.feelings = feelings
        # The Work panel's cards of these tasks, read only (`cards(task_ids)`, playmuse.work_cards).
        self.cards = cards
        self._lock = threading.RLock()
        self._growing = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- kept ----------------------------------------------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self.folder / "world.json"

    def load(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            try:
                world = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            regions.migrate(world, CELL_W, CELL_H)       # a world from before regions: all of it the village
            if world.get("placed") != 2:
                self._place_again(world)
                self.save(world)                     # once: kept so
            parted = self._ids_apart(world)
            down = self._private_taken_down(world)
            if self._repeated_shots_down(world) or down or parted:
                self.save(world)
            return world

    @staticmethod
    def _repeated_shots_down(world: Dict[str, Any]) -> int:
        """ONE PICTURE ON ONE THING (2026-11-20: a page's own picture was found for nine things): a found picture alike
        one hung before it let go from the later thing - looked for again. How many."""
        kept: List[int] = []
        down = 0
        for item in sorted(world.get("items") or [], key=lambda one: str(one.get("made") or "")):
            if not item.get("shot") or not item.get("print"):
                continue
            try:
                made = int(str(item["print"]), 16)
            except ValueError:
                continue
            if any(prints_alike(made, one) for one in kept) and str(item.get("shot_of") or "").startswith("found-"):
                for key in ("shot", "shot_of", "shot_source", "shot_what", "print", "print_colour"):
                    item.pop(key, None)
                item.pop("find_tries", None)
                down += 1
                continue
            kept.append(made)
        if down:
            log.info("pictures hung on more than one thing let go from the later ones: %d", down)
        return down

    @staticmethod
    def _private_taken_down(world: Dict[str, Any]) -> int:
        """A PRIVATE SCREEN HUNG BEFORE, TAKEN DOWN (private_screen; 2026-11-20): the thing kept, its picture let go -
        looked for again where it asked for one. How many."""
        down = 0
        for item in world.get("items") or []:
            if item.get("shot") and private_screen({"what": item.get("shot_what")}):
                for key in ("shot", "shot_of", "shot_source", "shot_what", "print"):
                    item.pop(key, None)
                item.pop("find_tries", None)
                down += 1
        if down:
            log.info("private screens taken down from the play world's things: %d", down)
        return down

    def _ids_apart(self, world: Dict[str, Any]) -> int:
        """TWO THINGS UNDER ONE ID, PARTED (new_item_id): the later one given a new id; a picture file the other names
        too is left to that one - this one's let go, to be looked for again - one of its own copied to its new name.
        How many were parted."""
        items = world.get("items") or []
        seen: Dict[str, Dict[str, Any]] = {}
        parted = 0
        for item in items:
            ident = str(item.get("id") or "")
            if ident not in seen:
                seen[ident] = item
                continue
            other, new = seen[ident], new_item_id(world)
            item["id"] = new
            parted += 1
            for key, fields in (("shot", ("shot", "shot_of", "shot_source", "shot_what", "print")),
                                ("picture", ("picture", "picture_of", "picture_source"))):
                name = str(item.get(key) or "")
                if not name.startswith(("shot-" + ident + ".", ident + ".", "found-" + ident + ".")):
                    continue
                if name == str(other.get(key) or ""):
                    for field in fields:
                        item.pop(field, None)
                    if key == "picture":
                        item["picture"] = ""
                    item.pop("find_tries", None)
                    continue
                renamed = name.replace(ident + ".", new + ".", 1)
                try:
                    shutil.copyfile(self.folder / "pictures" / name, self.folder / "pictures" / renamed)
                    item[key] = renamed
                except OSError:
                    item.pop(key, None)
            if not item.get("look_for"):
                item.pop("find_tries", None)          # counted for the other, never for this one
        if parted:
            log.info("the play world's things parted from ids they shared: %d", parted)
        return parted

    def _place_again(self, world: Dict[str, Any]) -> None:
        """A WORLD LAID ON A GRID, PLACED AGAIN (the user, 2026-11-17: "重新摆"): in the order they were made, each place
        where there is room near its region's heart or the places before it - its things moved with it, as they stood;
        nothing lost. Once."""
        placed: List[Dict[str, Any]] = []
        for zone in sorted(world.get("zones") or [], key=lambda one: one.get("made", "")):
            zone.setdefault("span", list(places.span_of(zone)))
            region = next((one for one in world.get("regions") or [] if one["id"] == zone.get("region")),
                          (world.get("regions") or [{}])[0])
            if not region:
                placed.append(zone)
                continue
            anchor = self._anchor(world, region, zone["id"], placed=placed)
            spot = places.find_spot(world, region, zone["kind"], zone["w"], zone["h"], anchor, zone["id"],
                                    [places.zone_rect(one) for one in placed])
            if spot:
                dx, dy = spot[0] - zone["x"], spot[1] - zone["y"]
                zone["x"], zone["y"] = spot
                if zone.get("door"):
                    zone["door"] = int(zone["door"]) + dx
                for item in world.get("items") or []:
                    if item.get("zone") == zone["id"]:
                        item["x"], item["y"] = item["x"] + dx, item["y"] + dy
            zone.pop("cell", None)
            zone.pop("cells", None)
            placed.append(zone)
        world["placed"] = 2

    def save(self, world: Dict[str, Any]) -> None:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            spare = self.path.with_suffix(".json.new")
            spare.write_text(json.dumps(world, ensure_ascii=False, indent=1), encoding="utf-8")
            spare.replace(self.path)

    def world(self) -> Dict[str, Any]:
        """The world, made the first time it is asked for: a home, nothing in it but a bed and a window. THERE, IT IS
        DRAWN ON; NOT THERE - never made, or taken away - IT IS DRAWN FROM THE START (the user, 2026-11-17: "之前画的被删掉了，
        当前没有内容的时候就重画，不然就接着画"). No redrawing otherwise: no button, no version of the rules."""
        with self._lock:
            world = self.load()
            if world is not None:
                return world
            now = _now()
            start = self._start(now)
            world = {"version": 1, "born": now, "grown_at": "", "caught_up": start, "drawn_from": start, "zones": [],
                     "items": [], "growths": []}
            regions.migrate(world, CELL_W, CELL_H)
            # A HOUSE, NOT A VILLAGE (the user, 2026-11-19: "不要从起始村，从一个起始房子，是看不到外面的那种"): the first land
            # closed - only the house is there, no world round it - till the feeling of their days opens it
            # (_open_outside), its land imagined from that feeling.
            if self.START_CLOSED:
                world["regions"][0].update(closed=True, name="Home")
            world["placed"] = 2
            home = self._add_zone(world, "home", "Home", "Where you start and end each day.", now)
            for kind, why in (("bed", "Every day ends here, so home begins with a bed."),
                              ("window", "A window to see the world we are about to grow."),
                              ("lamp", "A lamp, for the evenings you work late.")):
                self._add_item(world, home, {"kind": kind, "name": "", "why": why, "from": ""}, now)
            self._starter_words(world)
            self.save(world)
            return world

    #: THE FIRST WORDS IN BOTH LANGUAGES (the user, 2026-11-19: "你初始的item中英翻译可以做好，这样一开始就可以切换"):
    #: what the code writes for a world just begun, put into Chinese here - switched from the first moment, nothing asked.
    STARTER_ZH = {
        "Home": "家",
        "Starting Village": "起始村庄",
        "Where everyone begins.": "每个人开始的地方。",
        "Where you start and end each day.": "你开始和结束每一天的地方。",
        "Every day ends here, so home begins with a bed.": "每一天都在这里结束，所以家从一张床开始。",
        "A window to see the world we are about to grow.": "一扇窗，看我们即将一起长出来的世界。",
        "A lamp, for the evenings you work late.": "一盏灯，陪你工作到很晚的夜里。",
        "Bed": "床", "Window": "窗户", "Lamp": "台灯",            # the things called by their kind (_words_of)
    }

    def _starter_words(self, world: Dict[str, Any]) -> None:
        """Each starting place, land and thing given its words in English and Chinese (STARTER_ZH) - kept as a
        translation would be (_translate), so it is never asked."""
        for _key, one in self._word_holders(world):
            fields = self._words_of(one)
            if fields and all(v in self.STARTER_ZH for v in fields.values()):
                one["words"] = {"en": dict(fields), "zh": {f: self.STARTER_ZH[v] for f, v in fields.items()},
                                "rule": TRANSLATE_RULE}

    def _start(self, now: str) -> str:
        """Where drawing begins: the earliest of what was kept, at most BACK_DAYS back."""
        from datetime import timedelta
        floor = (datetime.fromisoformat(now) - timedelta(days=BACK_DAYS)).isoformat(timespec="seconds")
        first = ""
        if callable(self.earliest):
            try:
                first = str(self.earliest() or "")
            except Exception:
                log.debug("the earliest of what was lived could not be read", exc_info=True)
        return max(first, floor) if first else floor

    @staticmethod
    def _today_began(now: str) -> datetime:
        """The start of the user's today (their own clock) - what is drawn reaches it: the day before, whole."""
        return datetime.fromisoformat(now).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)

    def window(self, world: Dict[str, Any], now: str) -> Tuple[str, str]:
        """The days the next growth draws: from where it was caught up to, STEP_DAYS on - UP TO THE DAY BEFORE (the
        user, 2026-11-18: "为什么你现在是画到11.9就不画了，要画到前一天为止"). Drawn through yesterday already (grown
        again today, as asked), today so far."""
        from datetime import timedelta
        since = str(world.get("caught_up") or world.get("born") or now)
        try:
            began, start = self._today_began(now), datetime.fromisoformat(since)
            if start >= began:
                return since, now
            until = min(start + timedelta(days=STEP_DAYS), began)
        except ValueError:
            return since, now
        return since, until.isoformat(timespec="seconds")

    def behind(self, world: Optional[Dict[str, Any]] = None, now: Optional[str] = None) -> bool:
        """Whether any day before today is not drawn yet - grown again soon till it is (it was: more than STEP_DAYS
        behind, so a world caught up to nine days ago waited for the next day)."""
        world = world or self.load() or {}
        try:
            return datetime.fromisoformat(str(world.get("caught_up") or "")) < self._today_began(now or _now())
        except ValueError:
            return False

    # -- laid out: the same each time ------------------------------------------------------------------------------
    def _anchor(self, world: Dict[str, Any], region: Dict[str, Any], seed: str, near: str = "",
                placed: Optional[List[Dict[str, Any]]] = None) -> Tuple[float, float]:
        """Where a new place is drawn towards: the place it belongs with, if said; else, by chance, its region's heart or
        the place made last there - so places gather in loose clusters."""
        mine = [one for one in (placed if placed is not None else world["zones"]) if one.get("region") == region["id"]]
        by_id = {one["id"]: one for one in mine}
        if near in by_id:
            return places.centre(places.zone_rect(by_id[near]))
        if not mine:
            return region["ox"] + places.CELL_W / 2, region["oy"] + places.CELL_H / 2
        if places._seeded(seed, 0, 0) < 0.5:
            return places.heart({"zones": mine}, region)
        return places.centre(places.zone_rect(mine[-1]))

    def _add_zone(self, world: Dict[str, Any], kind: str, name: str, why: str, at: str,
                  region: Optional[Dict[str, Any]] = None, near: str = "",
                  towards: Optional[Tuple[float, float]] = None) -> Optional[Dict[str, Any]]:
        """A place where there is room in its region (the newest, unless said) - near what it belongs with, or towards
        a point given - None when the region has no room left."""
        region = region or world["regions"][-1]
        outdoor = bool(ZONE_KINDS.get(kind, {}).get("outdoor"))
        w, h = places.FIRST_OUTDOOR if outdoor else places.FIRST_INDOOR
        ident = new_zone_id(world)
        spot = places.find_spot(world, region, kind, w, h, towards or self._anchor(world, region, ident, near), ident)
        if spot is None:
            return None
        zone = {"id": ident, "kind": kind, "name": name[:60], "why": why[:400], "made": at, "region": region["id"],
                "span": [1, 1], "style": FIRST_STYLE.get(kind, ""), "x": spot[0], "y": spot[1], "w": w, "h": h,
                "grown": []}
        # ITS DOOR WHERE IT WAS MADE: a room grown wider keeps it - what stands beside it never ends in its way.
        zone["door"] = zone["x"] + 1 + w // 2
        world["zones"].append(zone)
        if kind == "plaza":                           # its fountain in its middle
            world["items"].append({"id": new_item_id(world), "zone": zone["id"], "kind": "fountain",
                                   "x": zone["x"] + w // 2 - 1, "y": zone["y"] + 1 + h // 2 - 1, "name": name[:60],
                                   "why": f"A square needs its fountain - {name[:40]} gathers round it.", "from": "",
                                   "made": at, "picture": ""})
        if kind == "grove":                           # its trees, scattered through it, a step apart
            fx, fy = zone["x"], zone["y"] + 1
            taken = set()
            for n in range(14):
                tx, ty = fx + 1 + (n * 5) % max(1, w - 2), fy + (n * 3 + n // 3) % max(1, h - 1)
                if any(abs(tx - a) < 2 and abs(ty - b) < 2 for a, b in taken):
                    continue
                taken.add((tx, ty))
                world["items"].append({"id": new_item_id(world), "zone": zone["id"],
                                       "kind": ("tree", "pine", "tree", "bush")[n % 4], "x": tx, "y": ty, "name": "",
                                       "why": f"One of the trees of {name[:40]}.", "from": "", "made": at, "picture": ""})
        return zone

    @staticmethod
    def floor_of(zone: Dict[str, Any]) -> Tuple[int, int, int, int]:
        """A zone's floor: left, top, width, height in tiles (inside its walls)."""
        if ZONE_KINDS.get(zone["kind"], {}).get("outdoor"):
            return zone["x"], zone["y"] + 1, zone["w"], zone["h"]
        return zone["x"] + 1, zone["y"] + 3, zone["w"], zone["h"]

    @staticmethod
    def door_of(zone: Dict[str, Any]) -> int:
        """The column of a room's door, in its bottom wall - where it was made."""
        return int(zone.get("door") or zone["x"] + 1 + zone["w"] // 2)

    def _place(self, world: Dict[str, Any], zone: Dict[str, Any], kind: str) -> Optional[Tuple[int, int]]:
        """Where a thing of `kind` fits in `zone` - on its wall or its floor, the door's way and the row before it
        kept free; None when it is full."""
        size = ITEM_KINDS[kind]["size"]
        outdoor = ZONE_KINDS.get(zone["kind"], {}).get("outdoor")
        taken = set()
        for one in world["items"]:
            if one["zone"] != zone["id"]:
                continue
            w, h = item_size(one)
            taken |= {(one["x"] + dx, one["y"] + dy) for dx in range(w) for dy in range(h)}
        taken |= places.inner_walls(zone) | places.pool_water(zone)
        if places.inner_walls(zone):                   # a doorway in an inside wall kept clear on both sides
            walls = places.inner_walls(zone)
            left, top, w, h = self.floor_of(zone)
            for x in range(left, left + w):
                for y in range(top, top + h):
                    if (x, y) not in walls and (((x - 1, y) in walls and (x + 1, y) in walls)
                                                or ((x, y - 1) in walls and (x, y + 1) in walls)):
                        taken |= {(x, y), (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)} - walls
        if ITEM_KINDS[kind].get("wall") and not outdoor:
            top = zone["y"] + 1 if size[1] >= 2 else zone["y"] + 1
            for x in range(zone["x"] + 1, zone["x"] + 1 + zone["w"] - size[0] + 1):
                spots = {(x + dx, top + dy) for dx in range(size[0]) for dy in range(size[1])}
                if not spots & taken:
                    return x, top
            if kind != "frame":
                return None                           # a frame may stand on the floor when the walls are full
        left, top, w, h = self.floor_of(zone)
        door = None if outdoor else self.door_of(zone)
        # SPREAD ROUND A BUILDING'S ROOMS: the room inside it with fewest things first (one room, for one cell).
        rooms = places.rooms_of(zone, (left, top, w, h))
        mine = [(one["x"], one["y"]) for one in world["items"] if one["zone"] == zone["id"]]
        rooms.sort(key=lambda r: sum(1 for x, y in mine if r[0] <= x < r[2] and r[1] <= y < r[3]))
        for rx0, ry0, rx1, ry1 in rooms:
            for y in range(ry0, min(ry1, top + h - 1) - size[1] + 1):   # the floor's last row kept: the way along
                for x in range(rx0, rx1 - size[0] + 1):
                    spots = {(x + dx, y + dy) for dx in range(size[0]) for dy in range(size[1])}
                    if spots & taken or (door is not None and any(door <= sx < door + DOOR_W for sx, _sy in spots)):
                        continue
                    return x, y
        return None

    def _most(self, zone: Dict[str, Any]) -> Tuple[int, int]:
        outdoor = bool(ZONE_KINDS.get(zone["kind"], {}).get("outdoor"))
        return places.most_size(places.span_of(zone), CELL_W, CELL_H, outdoor)

    #: A room's floor for its cards (the user, 2026-11-19: "对于这种过分拥挤的，可以给与更大的位置"): this many tiles to
    #: begin with, and this many more for each card it holds - each card a thing or a few, with room to walk between.
    ROOM_BASE_TILES, TILES_A_CARD = 36, 9
    #: The blocks a room may grow over for its cards, smallest first (cells across, down).
    CARD_SPANS = ((1, 1), (2, 1), (1, 2), (2, 2), (3, 2), (2, 3), (3, 3), (4, 3), (4, 4))

    def _fit_to_cards(self, world: Dict[str, Any], zone: Dict[str, Any], cards: int) -> bool:
        """A ROOM AS LARGE AS ITS WORK, BEFORE IT IS DESIGNED: its floor - the tiles one can stand on, its outline's
        (a star, a triangle) and walls taken off - ROOM_BASE_TILES and TILES_A_CARD for each card; over more cells if
        one does not hold it; never made smaller, a great ground's part kept as its plan made it. WHERE IT STANDS NOT
        ENOUGH, IT IS MADE LARGER ALL THE SAME (the user, 2026-11-20: "原地不够是一定要扩大的，找个新的地方，哪怕生成新的土地也行";
        "不能原地能扩多大扩多大，一定要找新的土地expand， 新的土地沿用当前主题"): grown where it stands; else moved, whole, to
        free ground in its land (_resize); else to a land of its own opened beside the others, its theme the same
        (_district) - never grown only part of the way. Whether it grew."""
        if zone.get("plot") or zone.get("great") or zone.get("in_great") or not (cards or zone.get("shape")):
            return False
        want = self.ROOM_BASE_TILES + self.TILES_A_CARD * cards
        floor = lambda w, h: len(self._floor_tiles({**zone, "w": w, "h": h, "door": zone["x"] + 1 + w // 2}))  # noqa: E731
        if floor(zone["w"], zone["h"]) >= want:
            return False
        outdoor = bool(ZONE_KINDS.get(zone["kind"], {}).get("outdoor"))
        here = places.span_of(zone)

        def grown_to(span: Tuple[int, int]) -> Tuple[int, int]:
            most_w, most_h = places.most_size(span, CELL_W, CELL_H, outdoor)
            w, h = max(zone["w"], 1), max(zone["h"], 1)
            while floor(w, h) < want and (w < most_w or h < most_h):   # its shape kept: the shorter side grown first
                if (h < most_h and h * most_w <= w * most_h) or w >= most_w:
                    h += 1
                else:
                    w += 1
            return w, h

        spans = [s for s in self.CARD_SPANS if s[0] >= here[0] and s[1] >= here[1]]
        if not spans:
            return False
        # The smallest span that holds it (the largest when none does): its size.
        target = next(((s, grown_to(s)) for s in spans if floor(*grown_to(s)) >= want), None) \
            or (spans[-1], grown_to(spans[-1]))
        span, (w, h) = target
        if (w, h) == (zone["w"], zone["h"]):
            return False
        was = (zone["x"], zone["y"], zone.get("region"))
        how = ""
        if self._resize(world, zone, w, h, span):        # where it stands, or free ground in its land
            how = "where it stood" if (zone["x"], zone["y"]) == was[:2] else "moved to free ground in its land"
        elif self._to_new_land(world, zone, w, h, span):
            how = "moved to a land of its own"
        else:                                            # never left too small where it stands
            log.info("a room could not be made large enough anywhere: %s (%d x %d)", zone.get("name"), w, h)
            return False
        dx, dy = zone["x"] - was[0], zone["y"] - was[1]
        if dx or dy:
            for one in world["items"]:                  # moved to where it fits: its things with it
                if one.get("zone") == zone["id"]:
                    one["x"] += dx
                    one["y"] += dy
        zone.setdefault("grown", []).append({"at": "", "what": f"{zone['w']} x {zone['h']}", "how": how,
                                             "why": f"room for its {cards} cards" if cards else "room in its outline"})
        log.info("a room grown for its cards: %s to %d x %d (%s)", zone.get("name"), zone["w"], zone["h"], how)
        return True

    def _to_new_land(self, world: Dict[str, Any], zone: Dict[str, Any], w: int, h: int,
                     span: Tuple[int, int]) -> bool:
        """A room too large for any free ground in its land, moved to a land of its own (_district: beside the
        others, its land the same as where it stood, spread as far as it needs) - its size `w` x `h`. Whether it
        could be (its things are moved by the caller)."""
        owner = str(zone.get("task") or zone.get("part_of") or zone["id"])
        others = [places.zone_rect(one) for one in world["zones"] if one["id"] != zone["id"]]
        spot, region = None, None
        for more in (2, 4, 8, 16):                      # spread further while it does not hold it
            try:
                region = self._district(world, owner, zone, _now(), area=w * h * more)
            except Exception:
                log.info("no land of its own could be opened for %s", zone.get("name"), exc_info=True)
                return False
            if region.get("id") == zone.get("region"):
                return False
            spot = places.find_spot(world, region, zone["kind"], w, h, places.heart(world, region), zone["id"], others)
            if spot is not None:
                break
        if spot is None or region is None:
            return False
        zone.update(x=spot[0], y=spot[1], w=w, h=h, span=list(span), region=region["id"])
        zone["door"] = zone["x"] + 1 + zone["w"] // 2
        # Its own grounds go with it, beside it in the new land, their things with them.
        for ground in [one for one in world["zones"] if one.get("grounds_of") == zone["id"]]:
            before = ground.get("region")
            ground["region"] = region["id"]
            if not self._move(world, ground, (zone["x"] + zone["w"] / 2, zone["y"] + zone["h"] / 2)):
                ground["region"] = before
        return True

    def _grow_room(self, world: Dict[str, Any], zone: Dict[str, Any], at: str, why: str) -> bool:
        """A room full made larger within its cells - deeper first, then wider - its growth kept with its date.
        Whether it could be (beyond its cells it is the model's to enlarge or redesign)."""
        most_w, most_h = self._most(zone)
        if zone["h"] < most_h:
            zone["h"] += 1
        elif zone["w"] < most_w:
            zone["w"] += 1
        else:
            return False
        zone["grown"].append({"at": at, "what": f"{zone['w']} x {zone['h']}", "why": why[:300]})
        return True

    def _add_item(self, world: Dict[str, Any], zone: Dict[str, Any], said: Dict[str, Any], at: str,
                  picture: str = "") -> Optional[Dict[str, Any]]:
        kind = said["kind"]
        if alive(kind):
            return None                                  # an animal walks about on its own, never stands still here
        spot = self._place(world, zone, kind)
        while spot is None and self._grow_room(world, zone, at, f"room for {said.get('name') or kind}"):
            spot = self._place(world, zone, kind)
        if spot is None:
            # FULL AT ITS LARGEST: nothing more here - no room beside it made by itself (a new place is for new
            # content only); the model is told it is full, to enlarge it or redesign it.
            return None
        item = {"id": new_item_id(world), "zone": zone["id"], "kind": kind,
                "x": spot[0], "y": spot[1], "name": str(said.get("name") or "")[:60],
                "why": str(said.get("why") or "")[:500], "from": str(said.get("from") or "")[:200],
                "made": at, "picture": picture, **({"task": str(said["task"])[:40]} if said.get("task") else {})}
        world["items"].append(item)
        return item

    # -- grown -----------------------------------------------------------------------------------------------------
    def grow(self, now: Optional[str] = None) -> Dict[str, Any]:
        """THE DAY'S GROWTH: what the user lived lately, and what is here, given the model; what it adds laid out and
        kept, each thing and room with its date. What was added: {"zones": n, "items": n} (or {"error"})."""
        # ITS OWN FUNCTION, SWITCHED OFF APART (the user, 2026-11-17: "生成world要作为一个单独的function, 能够关掉（计算token
        # 用量，关掉之后world还在，但是不能增长)"): off, the world is still there to walk in; nothing grows, no model asked.
        if not self.on():
            return {"error": "the play world's growing is switched off"}
        if self.paused():
            return {"error": "the play world's drawing is paused"}
        if not self._growing.acquire(blocking=False):
            return {"error": "growing already"}
        try:
            return self._grow(now or _now())
        finally:
            self._growing.release()

    def _grow(self, at: str) -> Dict[str, Any]:
        from ..common.languages import in_display
        from ..models.answers import read_text
        world = self.world()
        since, until = self.window(world, at)
        about = lived = ""
        if callable(self.context):
            try:
                about = str(self.context() or "")
            except Exception:
                log.debug("who the user is could not be read for the world", exc_info=True)
        if callable(self.history):
            try:
                lived = str(self.history(since, until) or "")
            except Exception:
                log.debug("what the user lived could not be read for the world", exc_info=True)
        offered = []
        if callable(self.pictures):
            try:
                offered = [one for one in list(self.pictures() or []) if not private_screen(one)][:MOST_OFFERED]
            except Exception:
                log.debug("the user's pictures could not be offered to the world", exc_info=True)
            # Not offered again: a picture shown already, or one alike it.
            shown = [made[0] for made in (self._print_of(one) for one in world["items"]) if made]
            if shown:
                offered = [one for one in offered
                           if not any(prints_alike((picture_print(one.get("path")) or (-1, 0))[0], s) for s in shown)]
        here = {"zones": [{"id": one["id"], "kind": one["kind"], "name": one["name"], "made": one["made"][:10],
                           **({"concept": one["concept"]} if one.get("concept") else {}),
                           **({"sparse": True} if self._sparse(world, one) else {}),
                           **({"free": f"{round(100 * self._free_share(world, one))}%"} if one.get("task") else {}),
                           "style": one.get("style", ""), "scale": "x".join(map(str, places.span_of(one))),
                           "music": (one.get("music") or {}).get("track") or ("its own tune" if one.get("music") else ""),
                           "size": f"{one['w']} x {one['h']}", **({"full": True} if self._full(world, one) else {})}
                          for one in world["zones"]],
                "items": [{"zone": one["zone"], "kind": one["kind"], "name": one["name"], "made": one["made"][:10]}
                          for one in world["items"] if not one.get("carried")][-80:]}
        current = world["regions"][-1]
        here["region_now"] = {key: current.get(key) for key in ("name", "starter", *regions.SETTINGS)}
        opening = regions.may_leave(world, self.drawn_days(world))
        looked = self._looked(since, until, lived, about)
        chosen = self._themes(since, until, lived, world)
        self._chosen = chosen
        important: List[Dict[str, Any]] = []
        if callable(self.tasks):
            try:
                important = list(self.tasks(since, until) or [])
            except Exception:
                log.debug("the important tasks could not be read for the world", exc_info=True)
        placed = {str(one.get("task")): one["name"] for one in world["zones"] if one.get("task")}
        for one in world["zones"]:                     # a great house holds the tasks merged into it
            for task in one.get("tasks") or []:
                placed.setdefault(str(task), one["name"])
        neighbourhoods = self._neighbourhoods(world)
        self._between = {tuple(one["zones"]): one for one in neighbourhoods}
        lines = self._lines(world)
        self._goes_on = {tuple(one["zones"]): one for one in lines}
        for one in important:                          # their private screens never among them (private_screen)
            one["pictures"] = [p for p in one.get("pictures") or [] if not private_screen(p)]
            for step in one.get("story") or []:
                step["pictures"] = [p for p in step.get("pictures") or [] if not private_screen(p)]
            offered.extend(one.get("pictures") or [])
        # THE STORY NOT YET TOLD: each project's steps with no station in the world yet, and their pictures.
        told = {(str(one.get("story_of")), str(one.get("story_step"))) for one in world["items"] if one.get("story_of")}
        for one in important:
            one["story_untold"] = [step for step in one.get("story") or [] if (str(one["id"]), step["step"]) not in told]
            for step in one["story_untold"]:
                offered.extend(step.get("pictures") or [])
        # THEIR PICTURES FOR THE ROOMS TOO (the user, 2026-11-19: "要让agent知道可以加入你自己的成果之类的信息（你现在好像
        # 没有用户的图片了），比如挂自己的图片"): each task's own, and those of the chat for any room (_room_pictures).
        self._pictures_by_task = {str(one["id"]): list(one.get("pictures") or [])
                                  + [p for step in one.get("story") or [] for p in step.get("pictures") or []]
                                  for one in important}
        self._pictures_anywhere = [p for p in offered if not any(p in v for v in self._pictures_by_task.values())]
        self._sources_by_task = {str(one["id"]): list(one.get("sources") or []) for one in important}
        # AS THE TASKS WENT: joined, merged, split (task_lineage) - before anything is drawn of these days.
        if callable(self.lineage):
            try:
                self._follow(world, self.lineage() or {})
            except Exception:
                log.info("the world could not follow its tasks' joins and splits", exc_info=True)
        # EACH TASK'S DAYS, KEPT (the user: "出现在同一天的task有边"): what the world's shape is laid from.
        book = world.setdefault("tasks", {})
        for one in important:
            record = book.setdefault(str(one["id"]), {})
            record["title"] = one["title"]
            record["minutes"] = max(float(record.get("minutes") or 0), float(one.get("weight") or 0))
            if one.get("measures"):                     # its complexity and how full its places are kept (fill_of)
                record["measures"], record["complexity"], record["fill"] = one["measures"], one["complexity"], one["fill"]
            kept_days = dict(record.get("day_minutes") or {})
            for d, m in (one.get("day_minutes") or {}).items():
                kept_days[d] = max(float(kept_days.get(d) or 0), float(m))        # read again: never counted twice
            record["day_minutes"] = {d: round(kept_days[d], 1) for d in sorted(kept_days)[-DAYS_KEPT:]}
            record["days"] = sorted(record["day_minutes"])
        # Small tasks apart: grouped by the model into whole scenes (none of them a place of its own).
        small = [one for one in important if one.get("small")]
        important = [one for one in important if not one.get("small")]
        self._small_ids = {str(one["id"]) for one in small if str(one["id"]) not in placed}
        self._small_titles = {str(one["id"]): one["title"] for one in small}
        self._small_minutes = {str(one["id"]): float(one.get("weight") or 0) for one in small}
        self._important = {one["id"] for one in important if one["id"] not in placed}
        self._concepts = self._conceive(world, important, small, lived, since, until)
        self._important_info = {one["id"]: one for one in important}
        # THE FEELING OF THESE DAYS, THEN THE MUSES (agent/playmuse.py): the scene of a new land, and what its places
        # are designed from.
        self._mood_now = self._mood(world, since, until)
        self._mood_opens = self._mood_opens_land(world, self._mood_now, until)
        if self._mood_now and not self._mood_opens:
            # THE LAND IT STAYS IN SAYS IT (the user, 2026-11-19: "每个区域的title, 要加上用户心情，和agent说的话"): these
            # days' feeling and the words to them on the land of that feeling - its look kept as it was built.
            land = next((r for r in reversed(world.get("regions") or []) if r.get("scene") == self._mood_now.get("scene")), None)
            if land is not None:
                land.update({k: self._mood_now[k] for k in ("feeling", "to_them") if self._mood_now.get(k)})
                land.setdefault("look", self._mood_now.get("look") or "")
        if self._mood_opens and not opening:
            opening = "feeling"
        # THE HOUSE ALONE TILL ENOUGH IS KNOWN (the user, 2026-11-19: "一开始数据不足的时候，先装饰home, 不要画外景，等有足够数据
        # （心情）再设置初始外景，第一次走出"): while the first land is closed, only the house is furnished; a feeling read
        # from enough of their days opens it, as the land that feeling is.
        self._closed = bool(world.get("regions") and world["regions"][0].get("closed"))
        if self._closed and self._mood_now and int(self._mood_now.get("evidence") or 0) >= muse.FIRST_LAND_EVIDENCE:
            self._open_outside(world, self._mood_now, at)
            self._closed = False
        if self._closed:
            opening, self._mood_opens = None, False
        self._muses = self._muse(world, self._mood_now)
        # EACH TASK'S WORK AS A TREE (the user, 2026-11-19): the Work panel's, else one made and kept.
        self._trees = self._trees_of(world, [one["id"] for one in important] + [one["id"] for one in small], at)
        liked = ""
        if callable(self.tastes):
            try:
                liked = str(self.tastes() or "")[:3000]
            except Exception:
                log.debug("their tastes could not be read for the world", exc_info=True)
        ask = {"today": at[:10], "the_world_now": here, **({"a_new_region": opening} if opening else {}),
               "these_days": f"{since[:10]} to {until[:10]}", "what_they_lived_these_days": lived[:12000],
               **({"looked_up": looked} if looked else {}),
               **({"their_tastes": liked} if liked else {}),
               **({"neighbourhoods": [{"zones": one["zones"], "projects": one["projects"]} for one in neighbourhoods]}
                  if neighbourhoods else {}),
               **({"lines": [{"zones": one["zones"], "from": one["from"], "to": one["to"]} for one in lines]}
                  if lines else {}),
               **({"small_tasks": [{"id": one["id"], "title": one["title"], "summary": one["summary"][:140],
                                    **({"leisure": True} if one.get("leisure") else {}),
                                    **({"its_place": placed[one["id"]]} if one["id"] in placed else {})}
                                   for one in small]} if small else {}),
               **({"important_tasks": [{"id": one["id"], "title": one["title"], "summary": one["summary"],
                                        **({"leisure": True} if one.get("leisure") else {}),
                                        "its_place": placed.get(one["id"], ""), "materials": one["materials"],
                                        "things_to_add": one.get("things", 3),
                                        "plot": "{} x {}".format(*plot_size(float(one.get("weight") or 0))),
                                        "complexity": "high" if one.get("complexity", 0) >= 20 else "medium"
                                        if one.get("complexity", 0) >= 10 else "low",
                                        "free_at_most": f"{round(100 * (1 - float(one.get('fill') or 0)))}%",
                                        **({"open_ground": self._open_ground(world, one["id"])} if self._open_ground(world, one["id"]) else {}),
                                        **({"story": [{key: step[key] for key in ("step", "from", "to", "did", "said")}
                                                      | {"pictures": [{"id": p["id"], "what": p["what"], "when": p["when"],
                                                                       **({"plain": True} if p.get("plain") else {})}
                                                                      for p in step.get("pictures") or []]}
                                                      for step in one.get("story_untold") or []]}
                                           if one.get("story_untold") else {}),
                                        "pictures": [p["id"] for p in one.get("pictures") or []],
                                        **({"work_tree": [{"id": c["id"], "title": c["title"], "depth": c["depth"],
                                                           "status": c["status"]} for c in self._trees[one["id"]][:80]]}
                                           if self._trees.get(one["id"]) else {})}
                                       for one in important]} if important else {}),
               "who_they_are_now": about[:4000],
               "THINGS_BY_THEME": catalogue.by_theme(chosen),
               **({"concepts": self._concepts} if self._concepts else {}),
               **({"only_home": world["zones"][0]["id"]} if self._closed and world.get("zones") else {}),
               **({"muses": {name: {k: v for k, v in one.items() if k not in ("pixels",)} | (
                   {"painting": {k: v for k, v in one["painting"].items() if k != "pixels"}} if one.get("painting") else {})
                   for name, one in self._muses.items()}} if self._muses else {}),
               "PICTURES": [{"id": one["id"], "when": one.get("when", ""), "what": one.get("what", "")[:300]}
                            for one in offered]}
        system = SYSTEM.format(zones=MOST_NEW_ZONES, items=MOST_NEW_ITEMS, zone_kinds=", ".join(k for k in ZONE_KINDS if k not in CODE_KINDS),
                               biomes=", ".join(regions.BIOMES), layouts=", ".join(regions.LAYOUTS),
                               palettes=", ".join(regions.PALETTES), themes=", ".join(regions.THEMES),
                               architectures=", ".join(regions.ARCHITECTURES),
                               landmarks=", ".join(regions.LANDMARKS), weathers=", ".join(regions.WEATHERS),
                               lights=", ".join(regions.LIGHTS), catchable=", ".join(CATCHABLE),
                               instruments=", ".join(INSTRUMENTS), arrangements=", ".join(ARRANGEMENTS),
                               tracks="; ".join(f"{k} ({v})" for k, v in TRACKS.items()),
                               indoor_kinds=", ".join(k for k in ZONE_KINDS if k not in places.OUTDOOR and k not in CODE_KINDS),
                               outdoor_kinds=", ".join(k for k in places.OUTDOOR if k not in CODE_KINDS), styles=", ".join(places.STYLES),
                               scales=", ".join(places.SCALES), enlarged=MOST_ENLARGED, redesigned=MOST_REDESIGNED,
                               item_kinds=", ".join(k for k in ITEM_KINDS if not ITEM_KINDS[k].get("thing")),
                               signs=", ".join(SIGN_STYLES), forms=", ".join(FORMS),
                               outlines="; ".join(f"{k} ({v})" for k, v in shapes.OUTLINES.items()),
                               roads=", ".join(muse.ROAD_MATERIALS))
        # WHAT WAS WORKED OUT BEFORE THE CALL, KEPT (2026-11-18: each task's minutes, complexity and fill, the projects
        # the tasks were grouped into, the joins and splits followed - all written on this copy of the world, and lost
        # when `apply` read the world again: no world ever kept "tasks" or "projects", so no place was filled to its
        # complexity and Personal Agent and WaterMelon were never one project).
        with self._lock:
            self.save(world)
        try:
            response = self.models.invoke(ModelRequest(
                system=system, task=TASK, max_tokens=12000, builtin_web=False,
                messages=[{"role": "user", "content": json.dumps(ask, ensure_ascii=False)}],
                language=in_language(self._play_code(), '"name", "why" and "from"')), role="primary")
            text, _cut = read_text(response)
            match = re.search(r"\{.*\}", text or "", re.DOTALL)
            said = json.loads(match.group(0)) if match else {}
        except Exception as exc:
            log.info("the world could not grow", exc_info=True)
            return {"error": str(exc)[:200]}
        self._pick(said)
        grown = self.apply(said, offered, at, (since, until))
        if "error" not in grown:
            try:
                made = self._great_grounds(at)
                if made:
                    grown["great_grounds"] = made
            except Exception:
                log.info("a project's great ground could not be designed", exc_info=True)
            try:
                self._world_events(at)
            except Exception:
                log.info("the world's events could not be found", exc_info=True)
            try:
                rooms = self._design_rooms(at)
                if rooms:
                    grown["rooms"] = rooms
            except Exception:
                log.info("the world's rooms could not be designed", exc_info=True)
            try:
                if self._finish_pieces(at):              # every great piece with its own design (drawn)
                    grown["pieces_finished"] = True
            except Exception:
                log.info("the great pieces could not be finished", exc_info=True)
            try:
                self._find_soon(at)                      # the pictures asked for, looked for beside what follows
            except Exception:
                log.info("the pictures asked for could not be found", exc_info=True)
            try:
                with self._lock:                         # every task's place showing the world then
                    fresh = self.world()
                    if self._place_news(fresh, at):
                        self.save(fresh)
            except Exception:
                log.info("the world then could not be shown", exc_info=True)
            try:
                self._design_transport(at)
            except Exception:
                log.info("the world's ways could not be designed", exc_info=True)
            try:
                self._civic(at)                          # a city's own buildings (2026-11-20)
            except Exception:
                log.info("a city's own buildings could not be built", exc_info=True)
            try:
                self._aesthetics(at)                     # the world's looks as a whole: joins, road edges, the night
            except Exception:
                log.info("the world's looks could not be designed", exc_info=True)
            try:
                # WHAT IS NOT YET IN THE LANGUAGE SHOWN, ALL OF IT (the user, 2026-11-19): written in the display language
                # it needs nothing; written before in another, it is put into it.
                self.translate_all()
            except Exception:
                log.info("the world's words could not be put in the language shown", exc_info=True)
        return grown

    def _conceive(self, world: Dict[str, Any], important: List[Dict[str, Any]], small: List[Dict[str, Any]],
                  lived: str, since: str, until: str) -> List[Dict[str, Any]]:
        """EACH PROJECT CONCEIVED FIRST, IN A CALL OF ITS OWN (the user: "构思一个call, call好了之后再加item"): its
        associations - by its name, its scene, its time - the whole scene, its parts and what fills its ground; a
        concept it has kept unless its life changed. [] when there is nothing to conceive or the call fails."""
        from ..common.languages import in_display
        from ..models.answers import read_text
        if not important and not small:
            return []
        kept = {str(z.get("task")): z.get("concept") for z in world["zones"] if z.get("task") and z.get("concept")}
        now_ids = {one["id"] for one in important} | {one["id"] for one in small}
        before = []
        for z in world["zones"]:
            task = str(z.get("task") or "")
            if task and task not in now_ids and not z.get("plot") and not z.get("grounds_of") and \
                    task not in {one["task"] for one in before}:
                before.append({"task": task, "title": str(z.get("task_title") or z.get("name") or "")[:80]})
        so_far = [{"name": p.get("name", ""), "tasks": p.get("tasks", []), "summary": p.get("summary", "")}
                  for p in world.get("projects") or []][:PROJECTS_MOST]
        projects = [{"task": one["id"], "title": one["title"], "what": one.get("summary", "")[:240],
                     "ground": "{} x {}".format(*plot_size(float(one.get("weight") or 0))),
                     **({"leisure": True} if one.get("leisure") else {}),
                     **({"concept": kept[one["id"]]} if one["id"] in kept else {})} for one in important]
        try:
            response = self.models.invoke(ModelRequest(
                system=CONCEIVE_SYSTEM.format(kinds=", ".join(k for k in ITEM_KINDS if not ITEM_KINDS[k].get("thing"))),
                task=TASK_CONCEIVE, max_tokens=3000, builtin_web=False,
                messages=[{"role": "user", "content": json.dumps(
                    {"these_days": f"{since[:10]} to {until[:10]}", "what_they_lived_these_days": lived[:4000],
                     "tasks_now": projects,
                     **({"small_tasks": [{"task": one["id"], "title": one["title"]} for one in small]} if small else {}),
                     **({"drawn_before": before[:DRAWN_BEFORE_MOST]} if before else {}),
                     **({"projects_so_far": so_far} if so_far else {})},
                    ensure_ascii=False)}],
                language=in_language(self._play_code(), '"name", "summary", "idea" and "parts"')), role="primary")
            text, _cut = read_text(response)
            found = self._conceived(text or "")
        except Exception:
            log.info("the world's projects could not be conceived", exc_info=True)
            return []
        known = now_ids | {one["task"] for one in before}
        blocks: List[Dict[str, Any]] = []
        if not found.get("projects") and found.get("concepts"):          # said the old way: each a project of one
            found = {"projects": [{"name": "", "tasks": [one.get("task")], "blocks": [one]}
                                  for one in found["concepts"] if isinstance(one, dict)]}
        for said in [p for p in found.get("projects") or [] if isinstance(p, dict)][:PROJECTS_MOST]:
            tasks = [str(t) for t in said.get("tasks") or [] if str(t) in known]
            tasks += [str(b.get("task")) for b in said.get("blocks") or [] if isinstance(b, dict)
                      and str(b.get("task")) in known and str(b.get("task")) not in tasks]
            if not tasks:
                continue
            project = self._keep_project(world, tasks, str(said.get("name") or ""), str(said.get("summary") or ""))
            for block in [b for b in said.get("blocks") or [] if isinstance(b, dict)]:
                if str(block.get("task")) in tasks and str(block.get("idea") or "").strip():
                    blocks.append({**block, "project": project["id"] if project else ""})
        return blocks[:12]

    @staticmethod
    def _conceived(text: str) -> Dict[str, Any]:
        """THE PROJECTS AS SAID, HOWEVER THEY WERE WRITTEN (2026-11-20: once the answer held more than one JSON object
        and was read as one - "Extra data" - and the world had no projects, so no great grounds): every whole object
        in it (answers.json_objects), their "projects" and "concepts" joined; one written as a project by itself
        taken as one."""
        from ..models.answers import json_objects
        joined: Dict[str, Any] = {"projects": [], "concepts": []}
        for one in json_objects(text):
            if isinstance(one.get("projects"), list) or isinstance(one.get("concepts"), list):
                joined["projects"] += [p for p in one.get("projects") or [] if isinstance(p, dict)]
                joined["concepts"] += [c for c in one.get("concepts") or [] if isinstance(c, dict)]
            elif isinstance(one.get("blocks"), list):
                joined["projects"].append(one)
        if not joined["concepts"]:
            joined.pop("concepts")
        if not joined["projects"] and not joined.get("concepts"):
            raise ValueError("no projects in the answer")
        return joined

    def _keep_project(self, world: Dict[str, Any], tasks: List[str], name: str, summary: str) -> Optional[Dict[str, Any]]:
        """ONE PROJECT, KEPT: the one that already has any of these tasks takes them all (each task in one project
        only); else a new one. Its name and joined imagination as said now, the old kept when nothing is said."""
        projects = world.setdefault("projects", [])
        project = next((p for p in projects if set(p.get("tasks") or []) & set(tasks)), None)
        if project is None:
            project = {"id": f"p{1 + max([int(str(p['id'])[1:] or 0) for p in projects] or [0])}", "tasks": [],
                       "name": "", "summary": ""}
            projects.append(project)
        for other in projects:
            if other is not project:
                other["tasks"] = [t for t in other.get("tasks") or [] if t not in tasks]
        project["tasks"] = list(dict.fromkeys([*project["tasks"], *tasks]))
        if name.strip():
            project["name"] = " ".join(name.split())[:60]
        if summary.strip():
            project["summary"] = " ".join(summary.split())[:400]
        world["projects"] = [p for p in projects if p.get("tasks")]
        return project

    # -- the feeling of these days, the muses, each room designed (agent/playmuse.py) ---------------------------------
    def _play_code(self) -> str:
        """The language a call of the growth writes in: Play's own (_shown_language), else the display one - said at
        each call (`language=in_language(self._play_code(), ...)`), so the code's languages are read off its calls."""
        from ..common.languages import display_language
        return self._shown_language() or str(display_language(self.config) or "en")

    def _call(self, system: str, task: str, content: Any, most: int, words: str, web: bool = False,
              theirs: str = "") -> Dict[str, Any]:
        """One of the growth's own calls, its answer's JSON ({} when there is none) - WRITTEN IN PLAY'S OWN LANGUAGE (the
        user, 2026-11-19: "每次调用的话都要用play的语言，只是在一开始play语言等于display languagde"): its own setting, else
        the display language; written in it at once, not in another and put into it after."""
        from ..common.languages import in_display, in_language
        from ..models.answers import read_text
        shown = self._shown_language()
        guard = None
        if web and theirs:                               # what is theirs HELD by the web guard: never in a search
            from ..runtime.web_guard import WebGuard
            guard = WebGuard()
            guard.know(theirs, "user")
            # Known is only remembered; HELD is stopped - only what is secret (2026-11-20: "搜索检查只查密码邮箱之类，
            # 项目名人名都不拦"): a mail address, a number of theirs, a key.
            guard.hold(theirs)
        response = self.models.invoke(ModelRequest(
            system=system, task=task, max_tokens=most, builtin_web=web,
            **({"web_guard": guard} if guard is not None else {}),
            messages=[{"role": "user", "content": content}],
            language=in_language(shown, words) if shown else in_display(words)), role="primary")
        text, _cut = read_text(response)
        return muse.json_of(text or "")

    def _mood(self, world: Dict[str, Any], since: str, until: str) -> Optional[Dict[str, Any]]:
        """THE FEELING OF THESE DAYS (playmuse: their words in their chats, the rhythm of their days - nothing else):
        kept in world["moods"], each stretch's own. None when nothing could be read of them or the call failed."""
        if not callable(self.feelings):
            return None
        try:
            read = dict(self.feelings(since, until) or {})
        except Exception:
            log.debug("the feeling of these days could not be read for the world", exc_info=True)
            return None
        if not (read.get("their_words") or read.get("their_rhythm")):
            return None
        before = [{key: one.get(key) for key in ("since", "mood", "scene")}
                  for one in (world.get("moods") or [])[-muse.MOODS_TOLD:]]
        try:
            # 1. HOW THESE DAYS FELT, A PARAGRAPH - from their words and their days, and THE WEATHER OF THEIR FIELD
            # (the user, 2026-11-20: "找当时user主要activity的领域...在那个时候的大事件，infer整体的心情"): the fields of
            # their work then, that time's big events in them searched on the web - their words known to the search's
            # guard, so none of them goes out.
            work = [str(one.get("title") or "")[:80] for one in (getattr(self, "_important_info", None) or {}).values()
                    if one.get("title")][:MOOD_WORK_TOLD]
            asked = {"these_days": f"{since[:10]} to {until[:10]}", **read, **({"their_work": work} if work else {})}
            written = self._call(muse.MOOD_SYSTEM.format(), muse.TASK_MOOD, json.dumps(asked, ensure_ascii=False),
                                 2500, '"mood", "events" and "feeling"', web=True,
                                 theirs=json.dumps({k: v for k, v in asked.items() if k != "these_days"},
                                                   ensure_ascii=False))
            paragraph = " ".join(str((written or {}).get("mood") or "").split())
            if not paragraph:
                return None
            fields = [{k: " ".join(str(one.get(k) or "").split())[:300] for k in ("field", "events", "feeling")}
                      for one in (written or {}).get("fields") or [] if isinstance(one, dict) and one.get("field")][:3]
            # 2. THE LAND THAT PARAGRAPH IS - from the paragraph alone (and the stretches before): its theme, then a
            # scene of it not used since the theme last came round (muse.scenes_open) - the land it is in now kept open.
            lands = [str(r["scene"]) for r in world.get("regions") or [] if r.get("scene")]
            open_now = muse.scenes_open(lands, keep=lands[-1] if lands else "")
            mood = muse.read_mood(self._call(
                muse.mood_scene_system(tracks="; ".join(f"{k} ({v})" for k, v in TRACKS.items()),
                                       instruments=", ".join(INSTRUMENTS), arrangements=", ".join(ARRANGEMENTS),
                                       open_now=open_now, used=lands),
                muse.TASK_MOOD_SCENE,
                json.dumps({"mood": paragraph, **({"before": before} if before else {})}, ensure_ascii=False),
                900, '"land_name" and "why"'), paragraph, open_now)
        except Exception:
            log.info("the feeling of these days could not be sensed for the world", exc_info=True)
            return None
        if mood is None:
            return None
        music = tune_of(mood.pop("music", None))         # the feeling's own music, checked
        if music:
            mood["music"] = music
        # How much it was read from - their words and their days - for the first land to wait for enough.
        mood.update(since=since[:10], until=until[:10],
                    evidence=len(read.get("their_words") or []) + len(read.get("their_rhythm") or []),
                    **({"fields": fields} if fields else {}))  # their field's weather then, as it was found
        world["moods"] = [*(world.get("moods") or []), mood][-muse.MOODS_KEPT:]
        return mood

    @staticmethod
    def _scene_life(region: Dict[str, Any], mood: Dict[str, Any]) -> None:
        """A feeling's land: the edge lands it runs out in, the animals that live there and its ways - as the agent
        chose them, each of its scene's own (playmuse.SCENE_LIFE, road_of)."""
        life = muse.life_of(mood["scene"], mood)
        region["edges"], region["animals"] = life["edges"], life["animals"]
        if mood.get("road"):
            region["road"] = mood["road"]

    @staticmethod
    def _land_own(region: Dict[str, Any], scene: str) -> None:
        """A land of a scene with its own houses, grounds and things (muse.SCENE_OWN - under the sea, in the sky, in space):
        kept with it, and told in its look to every design there."""
        own = muse.SCENE_OWN.get(scene)
        if own:
            region["own"] = own
            look = str(region.get("look") or "")
            if own not in look:
                region["look"] = (look + (" " if look else "") + f"Its own: {own}.")[:600]

    def _open_outside(self, world: Dict[str, Any], mood: Dict[str, Any], at: str) -> None:
        """THE WORLD OUTSIDE, FOR THE FIRST TIME: the closed first land made the land the feeling chose - its lie, its
        weather, its light, its name - the house standing in it, its mountain or its lake by it."""
        region = world["regions"][0]
        region.update(muse.scene_settings(mood["scene"]))
        region.update(name=mood.get("land_name") or region.get("name") or "", why=mood.get("why") or "",
                      scene=mood["scene"], mood=mood.get("mood", ""), opened=at, starter=False,
                      **{k: mood[k] for k in ("feeling", "to_them", "look", "shape") if mood.get(k)})
        if mood.get("since"):                            # its days in the world's time, as every later land's
            region["days"] = [str(mood["since"])[:10], str(mood.get("until") or mood["since"])[:10]]
        self._land_own(region, mood["scene"])
        region.pop("closed", None)
        if hasattr(regions, "fitting_landmark") and region.get("landmark"):
            region["landmark"] = regions.fitting_landmark(region["landmark"], region["biome"])
        world["outside_opened"] = at
        if mood.get("music"):                           # its music, as the feeling sounds
            region["music"] = mood["music"]
        self._scene_life(region, mood)
        nature = muse.MOOD_SCENES[mood["scene"]].get("nature")
        if nature in ZONE_KINDS:
            made = self._add_zone(world, nature, mood.get("land_name") or nature, mood.get("why") or "", at, region=region)
            if made is not None and mood.get("music"):  # the feeling's music, heard by it (play.js playNearestZone)
                made["music"] = mood["music"]

    def _mood_opens_land(self, world: Dict[str, Any], mood: Optional[Dict[str, Any]], until: str) -> bool:
        """A NEW LAND FOR A NEW FEELING (the user: "现在是小岛，之后又变成别的什么"): when the feeling's scene is not the
        land's, the feeling truly changed and the land has stood MOOD_LAND_DAYS days - the starting village left by
        its own rule (its new land takes the feeling's scene all the same). The lands before keep their looks."""
        from datetime import date
        if not mood or not world.get("regions"):
            return False
        # The land of the feeling now: the last one opened for a feeling - not a land opened for a project grown too
        # large (_district), which keeps the theme of where it began.
        current = next((r for r in reversed(world["regions"]) if not r.get("district_of")), world["regions"][-1])
        if current.get("starter") or current.get("scene") == mood["scene"]:
            return False
        if current.get("scene") and not mood.get("changed"):
            return False
        if not current.get("scene") and muse.MOOD_SCENES[mood["scene"]]["biome"] == current.get("biome"):
            return False
        # HOW LONG IT HAS STOOD, IN THE WORLD'S DAYS (2026-11-20: the first land kept the clock's day it was made - 11-19 -
        # and a stretch of September was always "before" it, so no feeling ever opened a new land): its own days; else
        # the first stretch whose feeling was its scene; else the day it was made, if not after these days; else where
        # the world was drawn from.
        made = str(current.get("made") or "")[:10]
        began = str((current.get("days") or [""])[0]
                    or next((m.get("since") for m in world.get("moods") or [] if m.get("scene") == current.get("scene")), "")
                    or (made if made and made <= until[:10] else "")
                    or world.get("drawn_from") or "")[:10]
        try:
            return (date.fromisoformat(until[:10]) - date.fromisoformat(began)).days >= muse.MOOD_LAND_DAYS
        except ValueError:
            return False

    def _muse(self, world: Dict[str, Any], mood: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """EACH PROJECT'S MUSES (playmuse.MUSE_SYSTEM: a poem, a piece of music, a painting, a video - found on the web
        or made): asked for the projects of these days that have none yet, kept in world["muses"] by project.
        {project name: its muses}, for these days' projects."""
        projects = {p["id"]: p for p in world.get("projects") or []}
        ideas: Dict[str, List[str]] = {}
        for block in getattr(self, "_concepts", None) or []:
            if block.get("project") in projects:
                ideas.setdefault(block["project"], []).append(" ".join(str(block.get("idea") or "").split())[:300])
        kept = world.setdefault("muses", {})
        wanted = [projects[i] for i in ideas if i not in kept][:MUSE_PROJECTS]
        if wanted:
            name_of = {p["id"]: p.get("name") or p["id"] for p in wanted}
            ask = {"projects": [{"name": name_of[p["id"]], "summary": p.get("summary", ""), "ideas": ideas[p["id"]][:4]}
                                for p in wanted]}
            try:
                found = self._call(muse.MUSE_SYSTEM.format(instruments=", ".join(INSTRUMENTS),
                                                           arrangements=", ".join(ARRANGEMENTS)),
                                   muse.TASK_MUSE, json.dumps(ask, ensure_ascii=False), 9000,
                                   '"title", "lines", "why" and "design"', web=True)
                read = muse.read_muses(found, name_of.values(), tune_of, drawn_pixels)
            except Exception:
                log.info("the world's muses could not be found", exc_info=True)
                read = {}
            by_name = {name: ident for ident, name in name_of.items()}
            for name, one in read.items():
                kept[by_name[name]] = one
        return {(projects[i].get("name") or i): kept[i] for i in ideas if i in kept}

    def _main_zone(self, world: Dict[str, Any], tasks: List[str]) -> Optional[Dict[str, Any]]:
        """A project's main place: its landmark, else its largest building or open place (not its ground)."""
        mine = [z for z in world["zones"] if str(z.get("task") or "") in tasks and not z.get("plot")]
        return max(mine, key=lambda z: (bool(z.get("landmark")), z["w"] * z["h"]), default=None)

    def _place_muses(self, world: Dict[str, Any], at: str) -> int:
        """THE MUSES PUT IN THE WORLD (the user: "可以放进去"): on each project's main place its card tells them (what
        was found, by its link); its tune plays there; the painting drawn after it stands there, and its poem on a
        post. Once. How many projects' were."""
        placed = 0
        for project in world.get("projects") or []:
            one = (world.get("muses") or {}).get(project["id"])
            if not one or one.get("placed"):
                continue
            zone = self._main_zone(world, [str(t) for t in project.get("tasks") or []])
            if zone is None:
                continue
            zone["muse"] = {key: ({k: v for k, v in one[key].items() if k not in ("pixels", "tune")}
                                  if isinstance(one[key], dict) else one[key])
                            for key in ("poem", "music", "painting", "video", "design") if one.get(key)}
            if (one.get("music") or {}).get("tune"):
                zone["music"] = one["music"]["tune"]
            painting = one.get("painting") or {}
            if painting.get("pixels") or painting.get("svg"):
                title = painting.get("title") or ""
                made = self._add_item(world, zone, {"kind": "pixel_art_large", "name": title, "from": "its muse",
                                                    "why": (f"After {title}" + (f" by {painting['by']}" if painting.get("by") else ""))
                                                    if title else ""}, at)
                if made is not None:
                    made.update(muse=True, **({"pixels": painting["pixels"]} if painting.get("pixels") else {}),
                                **({"svg": painting["svg"]} if painting.get("svg") else {}))
            poem = one.get("poem") or {}
            if poem.get("title") and "signpost" in ITEM_KINDS:
                made = self._add_item(world, zone, {"kind": "signpost", "name": poem["title"],
                                                    "why": poem.get("by") or "", "from": "its muse"}, at)
                if made is not None:
                    made.update(poem=poem.get("lines") or [], muse=True, **({"url": poem["url"]} if poem.get("url") else {}))
            one["placed"] = zone["id"]
            placed += 1
        return placed

    @staticmethod
    def _kept_in_room(one: Dict[str, Any]) -> bool:
        """What a room's design leaves where it is: the user's pictures, the project's story, what the agent drew,
        its muses."""
        return bool(one.get("picture") or one.get("story_of") or one.get("pixels") or one.get("muse"))

    @staticmethod
    def _room_size(kind: str) -> Optional[Tuple[int, int]]:
        spec = ITEM_KINDS.get(kind)
        if not spec or alive(kind) or spec.get("pixels"):
            return None
        return item_size({"kind": kind})              # a picture's kind too: laid only with one of theirs (_lay_room)

    @classmethod
    def _room_fit(cls, kind: str, open_air: bool = False) -> Optional[Tuple[int, int]]:
        """A kind's size if a room may hold it - INDOORS ANYTHING UP TO 2 x 5, either way round (the user, 2026-11-19:
        "所有内饰只要不超过2*5的大小都是可以的"; "只对室内生效"), standing, set on a table or hanging; an open place
        (a square, a garden) any size - its fountain, its greenhouse, its carousel. Else None."""
        if kind == "piece":
            # NOT AMONG A ROOM'S THINGS, ASKED FOR IN ITS "pieces" (2026-11-19: a lab laid a "piece" as a thing, tile
            # by tile, with no size or design, and the page could not draw it): a great piece is always designed and
            # built on its own (_build_pieces); one laid as a thing all the same is finished there (_finish_pieces).
            return None
        name = widgets.widget_of(kind)
        if name:                                         # a progress widget, where it may stand
            where = widgets.WIDGETS[name]["where"]
            return widgets.size_of(name) if where == "both" or where == ("open" if open_air else "inside") else None
        size = cls._room_size(kind)
        if not size or open_air:
            return size
        w, h = size
        return size if (w <= ROOM_THING_MOST[0] and h <= ROOM_THING_MOST[1]) or \
            (w <= ROOM_THING_MOST[1] and h <= ROOM_THING_MOST[0]) else None

    def _room_kinds(self, open_air: bool = False) -> str:
        """EVERY KIND A ROOM MAY BE FURNISHED WITH, FOR THE DESIGNER TO CHOOSE (the user, 2026-11-19: "房间设计要给他所有
        内饰的种类，让他自己选" - given a few, it hung lanterns everywhere): all there are that fit (_room_fit), by size."""
        by: Dict[Tuple[int, int], List[str]] = {}
        for kind in ITEM_KINDS:
            size = self._room_fit(kind, open_air)
            if size:
                by.setdefault(size, []).append(kind)
        return "; ".join(f"{w}x{h}: {', '.join(names)}" for (w, h), names in sorted(by.items()))

    def _design_rooms(self, at: str) -> int:
        """EACH IMPORTANT ROOM DESIGNED ON ITS OWN (the user approved, 2026-11-18): the muses put in the world first;
        then, the rooms of the longest tasks first (ROOMS_MOST), each designed tile by tile (play_world_room), drawn
        and looked at once (play_world_room_look), laid - the calls made outside the world's lock. How many were."""
        with self._lock:
            world = self.world()
            if self._place_muses(world, at):
                self.save(world)
        book = world.get("tasks") or {}
        whose = lambda z: str(z.get("task") or z.get("part_of") or (z.get("tasks") or [""])[0])   # noqa: E731
        # A ROOM OR AN OPEN PLACE, CARD BY CARD (the user, 2026-11-19: "露天和室内都是逐卡片的"): any place but a ground.
        # EVERY PLACE, A TASK'S OR NOT (the user, 2026-11-19: "你查一下为什么" - a stretch before any task had four rooms
        # of their life left undesigned): designed in layers all the same; a task's, card by card besides.
        rooms = [z for z in world["zones"] if (not z.get("plot") or z.get("great")) and not z.get("designed")
                 and (len(self._floor_tiles(z)) >= muse.ROOM_LEAST_TILES or z.get("shape"))]   # an outline: grown first
        rooms.sort(key=lambda z: -float((book.get(whose(z)) or {}).get("minutes") or 0))
        # NEW CARDS GATHERED, THEN DESIGNED IN (the user, 2026-11-19: "新卡攒够了要分批加入再设计"): a designed room's new
        # cards stand in as the code's plain things meanwhile; NEW_CARDS_LEAST of them in one room, and that room is
        # designed again for them - added to as it stands, a few at a time, the stand-ins taken away.
        stand_ins: Dict[str, List[Dict[str, Any]]] = {}
        for one in world["items"]:
            if one.get("stand_in") and one.get("card"):
                stand_ins.setdefault(one["zone"], []).append(one)
        again = [z for z in world["zones"] if z.get("designed") and not z.get("plot") and z not in rooms
                 and len(stand_ins.get(z["id"], [])) >= muse.NEW_CARDS_LEAST
                 and int(z.get("redo_tries") or 0) < muse.ROOM_TRIES_MOST]
        rooms = rooms + again
        if not rooms:
            with self._lock:
                fresh = self.world()
                self._card_index = {c["id"]: c for cards in (fresh.get("work_trees") or {}).values() for c in cards}
                if self._cover_cards(fresh, at):
                    self.save(fresh)
            return 0
        wall_kinds = {k for k, v in ITEM_KINDS.items() if v.get("wall")}      # a poster, a banner too: their pictures
        # Indoors and open places each their own list (_room_fit: the 2 x 5 limit indoors only).
        hangings = "; ".join(f"{theme}: {', '.join(names)}" for theme, names in sorted(THEME_HANGINGS.items()))
        self._library = self._made_library()
        systems = {open_air: muse.ROOM_SYSTEM.format(wall_kinds=", ".join(sorted(wall_kinds)), kinds=self._room_kinds(open_air),
                                                     widgets=widgets.widgets_told(open_air), hangings=hangings)
                   + "\n" + pieces.room_pieces_note(self._library) + "\n" + made_lib.room_made_note(self._library)
                   for open_air in (False, True)}
        mood = (world.get("moods") or [None])[-1]
        room_cards = self._room_cards(world, [z for z in rooms[:muse.ROOMS_MOST] if z not in again])
        done = 0
        for zone in rooms[:muse.ROOMS_MOST]:
            # A FEW CARDS AT A TIME, TILL EVERY ONE IS IN (the user, 2026-11-19: "每个设计一次性只能输入N个卡片（N是有上限的），
            # 但是可以多次输入，最终把每个都弄好"; "N稍微小一点"): the room designed with its first CARDS_A_CALL, then each
            # next few added to the room as it stands - each its design and its look, never an answer too long to read.
            cards = room_cards.get(zone["id"]) or []
            redo = zone in again
            if redo:                                    # its stand-ins' cards, the stand-ins taken away first
                index = {c["id"]: c for tree in (world.get("work_trees") or {}).values() for c in tree}
                cards = [index[one["card"]] for one in stand_ins[zone["id"]] if one["card"] in index]
                with self._lock:
                    fresh = self.world()
                    fresh["items"] = [one for one in fresh["items"]
                                      if not (one["zone"] == zone["id"] and one.get("stand_in"))]
                    self.save(fresh)
            if cards or zone.get("shape"):              # as large as its work (and its outline) first, then designed
                with self._lock:
                    fresh = self.world()
                    target = next((z for z in fresh["zones"] if z["id"] == zone["id"]), None)
                    if target is not None and self._fit_to_cards(fresh, target, len(cards)):
                        self.save(fresh)
            batches = [cards[n:n + muse.CARDS_A_CALL] for n in range(0, len(cards), muse.CARDS_A_CALL)] or [[]]
            laid_any = redo                             # a room designed before: added to, never laid again
            laid_now = False
            for n, batch in enumerate(batches):
                with self._lock:
                    now = self.world()
                target = next((z for z in now["zones"] if z["id"] == zone["id"]), None)
                if target is None:
                    break
                said, laid = self._design_room(now, target, batch, adding=laid_any, system=systems[not indoors(target)],
                                               wall_kinds=wall_kinds, mood=mood, whose=whose)
                if laid is None:
                    continue
                with self._lock:
                    fresh = self.world()
                    target = next((z for z in fresh["zones"] if z["id"] == zone["id"]), None)
                    if target is not None and self._lay_room(fresh, target, laid, at, str(said.get("idea") or ""),
                                                             str(said.get("looked") or ""), adding=laid_any):
                        self._surfaces(target, said)
                        self._order_by_time(fresh, target)       # its work's things along the way, in time
                        laid_any = laid_now = True
                        self.save(fresh)
                asked = (getattr(self, "_asked_pieces", {}) or {}).pop(zone["id"], [])
                if asked and laid_now:
                    try:
                        self._build_pieces(zone["id"], asked, at)
                    except Exception:
                        log.info("a great piece could not be built", exc_info=True)
                if laid_now:
                    self._find_soon(at)                  # its pictures looked for now, beside the rooms still to come
            with self._lock:
                fresh = self.world()
                target = next((z for z in fresh["zones"] if z["id"] == zone["id"]), None)
                if target is not None:
                    if laid_now:
                        target.pop("redo_tries", None)
                    elif redo:                          # its stand-ins come back (_cover_cards); tried again later
                        target["redo_tries"] = int(target.get("redo_tries") or 0) + 1
                    else:                               # tried and failed: tried again at most once more
                        target["design_tries"] = int(target.get("design_tries") or 0) + 1
                        if target["design_tries"] >= muse.ROOM_TRIES_MOST:
                            target["designed"] = at
                    self.save(fresh)
            if laid_now:
                done += 1
        with self._lock:
            fresh = self.world()
            if self._cover_cards(fresh, at):
                self.save(fresh)
        return done

    def _design_room(self, world: Dict[str, Any], zone: Dict[str, Any], cards: List[Dict[str, Any]], adding: bool,
                     system: str, wall_kinds: set, mood: Optional[Dict[str, Any]], whose: Callable[[Dict[str, Any]], str]
                     ) -> Tuple[Dict[str, Any], Optional[Dict[str, List[Dict[str, Any]]]]]:
        """ONE ROOM'S DESIGN FOR THESE CARDS (play_world_room), drawn and looked at once (play_world_room_look): the
        room as it is shown to the model - what stays marked on its floor, the wall columns taken - and, ADDING, every
        thing laid before staying. (what was said, what can be laid) - nothing laid when the calls failed."""
        floor = self._floor_tiles(zone)
        if zone.get("great"):                            # its halls stand on it: not its open ground
            for z in world["zones"]:
                if z.get("in_great") == zone["great"] and indoors(z):
                    x0, y0, x1, y1 = places.zone_rect(z)
                    floor -= {(x, y) for x in range(x0, x1) for y in range(y0, y1)}
        bottom = max(y for _x, y in floor) + 1
        clear = set(places.inner_doorways(zone))
        if self._has_door(zone):
            clear |= {(zone["door"] + d, y) for d in range(DOOR_W) for y in (bottom - 2, bottom - 1)}
        mine = [one for one in world["items"] if one["zone"] == zone["id"]]
        staying = [one for one in mine if adding or self._kept_in_room(one)]
        kept, wall_taken = set(), set()
        for one in staying:
            if (ITEM_KINDS.get(one["kind"]) or {}).get("wall"):
                wall_taken |= {x for x, _y in self._tiles_of(one)}
            elif not one.get("on") and not one.get("hang"):
                kept |= self._tiles_of(one)
        rows, left, top = muse.room_grid(floor, places.inner_walls(zone), clear, kept)
        project = next((p for p in world.get("projects") or [] if whose(zone) in (p.get("tasks") or [])), None)
        muses = (world.get("muses") or {}).get(project["id"]) if project else None
        open_air = not indoors(zone)
        wall_width = 0 if open_air else zone["w"]          # an open place: no wall, nothing hangs on one
        news = self._news_of(world, zone)                   # the world then, for the design to show
        ask = {"room": {"name": zone.get("name", ""), "kind": zone["kind"], "size": f"{len(rows[0])} x {len(rows)}",
                        **({"open_air": True} if open_air else {})},
               **({"concept": zone["concept"]} if zone.get("concept") else {}),
               **({"muses": {k: ({kk: vv for kk, vv in v.items() if kk not in ("pixels", "tune")} if isinstance(v, dict) else v)
                             for k, v in muses.items() if k != "placed"}} if muses else {}),
               **({"great_ground": self._great_brief(world, zone)} if zone.get("great") else {}),
               **({"adding": True, "there": [{"kind": one["kind"], "name": one.get("name", "")} for one in staying][:40],
                   "idea": zone.get("room_idea", "")} if adding else
                  {"said_for_it": [{"kind": one["kind"], "name": one.get("name", ""), "why": str(one.get("why") or "")[:160]}
                                   for one in mine if not self._kept_in_room(one) and not one.get("carried")][:30]}),
               **({"the_world_then": [{"date": e["date"], "headline": e["headline"]} for e in news]} if news else {}),
               **({"their_pictures": [{"id": p["id"], "what": p.get("what", ""), "when": p.get("when", "")}
                                      for p in theirs.values()]}
                  if (theirs := self._room_pictures(world, zone, whose(zone))) else {}),

               **({"cards": [{"id": c["id"], "title": c["title"], "done_when": c["statement"][:160],
                              "status": c["status"], "depth": c["depth"],
                              **({"first": c["first"]} if c.get("first") else {})}   # its way through, in time
                             for c in cards]} if cards else {}),
               "grid": rows, "wall_width": wall_width,
               "pieces_most": pieces.pieces_allowed(len(floor)),
               **({"land_look": land.get("look")} if (land := next((r for r in world.get("regions") or []
                                                                    if r["id"] == zone.get("region")), {})).get("look") else {}),
               **({"materials_nearby": nearby} if (nearby := sorted({f"{z.get('wall_material') or '-'} walls, "
                                                                     f"{z.get('floor_material') or '-'} floor"
                                                                     for z in world["zones"] if z["id"] != zone["id"]
                                                                     and z.get("region") == zone.get("region")
                                                                     and (z.get("wall_material") or z.get("floor_material"))}))
                  else {}),
               **({"wall_taken": sorted(x - left for x in wall_taken)} if wall_taken else {})}
        fit = lambda kind: self._room_fit(kind, open_air)   # noqa: E731
        read = lambda answer: muse.read_room(answer, rows, left, top, fit, wall_kinds, wall_width,   # noqa: E731
                                             wall_taken={x - left for x in wall_taken})
        most = great.GREAT_ANSWER_MOST if zone.get("great") else muse.ROOM_ANSWER_MOST
        if zone.get("great"):
            system = system + "\n" + GREAT_GROUND_NOTE
        try:
            said = self._call(system, muse.TASK_ROOM, json.dumps(ask, ensure_ascii=False), most,
                              '"idea", "name" and "why"')
            self._make_asked(said, zone, ask.get("land_look") or "")    # what it asked to be made, made first
            laid = read(said)
            things = [{**one, "layer": layer} for layer, group in laid.items() for one in group]
            things += [{**one, "layer": "wall" if (ITEM_KINDS.get(one["kind"]) or {}).get("wall") else "floor"}
                       for one in staying if not one.get("on") and not one.get("hang")]
            picture = muse.draw_room(rows, things, left, top, wall_width, self._room_size) if things else None
            if picture is not None and (laid["floor"] or laid["wall"]):
                again = self._call(muse.ROOM_LOOK_SYSTEM + "\n\n" + system, muse.TASK_ROOM_LOOK, [
                    {"type": "text", "text": json.dumps({"your_design": said, "grid": rows,
                                                         **({"adding": True} if adding else {})}, ensure_ascii=False)},
                    {"type": "image", "source": {"type": "base64", **picture}}], most,
                    '"idea", "looked", "name" and "why"')
                better = read(again)
                if better["floor"] or better["wall"]:
                    said, laid = again, better
        except Exception:
            log.info("a room of the world could not be designed", exc_info=True)
            return {}, None
        # THE GREAT PIECES IT ASKED FOR (agent/playpieces.py), on its free floor - built after it is laid (_build_pieces).
        claimed = {(x - left, y - top) for one in laid["floor"]
                   for x, y in {(one["x"] + dx, one["y"] + dy) for dx in range(fit(one["kind"])[0] if fit(one["kind"]) else 1)
                                for dy in range(fit(one["kind"])[1] if fit(one["kind"]) else 1)}}
        self._asked_pieces = getattr(self, "_asked_pieces", {})
        self._asked_pieces[zone["id"]] = pieces.read_asked(said, rows, left, top, claimed, pieces.pieces_allowed(len(floor)))
        return said, laid if (laid["floor"] or laid["wall"] or laid["on"] or laid["hang"] or laid["under"]) else None

    # -- each task's work as a tree of cards (playmuse; the user, 2026-11-19) ------------------------------------------
    def _trees_of(self, world: Dict[str, Any], tasks: List[str], at: str) -> Dict[str, List[Dict[str, Any]]]:
        """EACH TASK'S TREE: the Work panel's cards (read only) - else the one the world made for it before, kept and
        used again ("这个工作树可以保留，允许复用") - else one made now (play_world_tree), for the tasks that have none,
        TREES_A_CALL at a time. {task id: its cards in the tree's order, each with its depth}."""
        tasks = [str(t) for t in dict.fromkeys(tasks) if str(t)]
        panel: Dict[str, List[Dict[str, Any]]] = {}
        if callable(self.cards) and tasks:
            try:
                panel = dict(self.cards(tasks) or {})
            except Exception:
                log.debug("the work's cards could not be read for the world", exc_info=True)
        # KEPT APART FROM THE WORLD (the user, 2026-11-19: "我说的复用只要是play world 能复用就行"): in trees.json beside it,
        # so a world drawn again (world.json moved aside) still has them - made once, never paid for twice.
        made = self._kept_trees()
        made.update({t: one for t, one in (world.pop("trees", None) or {}).items() if t not in made})
        wanted = [t for t in tasks if not panel.get(t) and not (made.get(t) or {}).get("cards")]
        if wanted:
            info = getattr(self, "_important_info", {}) or {}
            small = getattr(self, "_small_titles", {}) or {}
            ask = {"tasks": [{"id": t, "title": (info.get(t) or {}).get("title") or small.get(t, ""),
                              "what": str((info.get(t) or {}).get("summary") or "")[:400],
                              "materials": [str(m.get("name") if isinstance(m, dict) else m)[:80]
                                            for m in ((info.get(t) or {}).get("materials") or [])][:12]}
                             for t in wanted[:muse.TREES_A_CALL]]}
            try:
                found = self._call(muse.TREE_SYSTEM.format(most=muse.TREE_MADE_MOST), muse.TASK_TREE,
                                   json.dumps(ask, ensure_ascii=False), 8000, '"title" and "statement"')
                for task, cards in muse.read_trees(found, wanted).items():
                    made[task] = {"made": at, "cards": cards}
            except Exception:
                log.info("the work's trees could not be made for the world", exc_info=True)
        self._keep_trees(made)
        trees = {}
        for task in tasks:
            cards = panel.get(task) or (made.get(task) or {}).get("cards") or []
            if cards:
                trees[task] = muse.forest(cards)
        world["work_trees"] = {**(world.get("work_trees") or {}), **{t: [{k: c[k] for k in (
            "id", "parent", "title", "statement", "type", "status", "depth", "minutes", "tries", "reworks", "first",
            "result") if k in c} for c in cards] for t, cards in trees.items()}}
        return trees

    def _kept_trees(self) -> Dict[str, Dict[str, Any]]:
        """The trees the world made for tasks without one, as kept beside it ({} when none)."""
        try:
            said = json.loads((self.folder / TREES_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return said if isinstance(said, dict) else {}

    def _keep_trees(self, trees: Dict[str, Dict[str, Any]]) -> None:
        if not trees:
            return
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            spare = (self.folder / TREES_FILE).with_suffix(".json.new")
            spare.write_text(json.dumps(trees, ensure_ascii=False, indent=1), encoding="utf-8")
            spare.replace(self.folder / TREES_FILE)

    def _card_named(self, card_id: str) -> Optional[Dict[str, Any]]:
        """A card of a tree of this growth (or kept in the world), by its id."""
        if not card_id:
            return None
        for cards in list((getattr(self, "_trees", None) or {}).values()):
            for one in cards:
                if one["id"] == card_id:
                    return one
        return getattr(self, "_card_index", {}).get(card_id)

    @staticmethod
    def _mark_card(item: Dict[str, Any], card: Optional[Dict[str, Any]]) -> None:
        """A thing standing for a card of the work: which, its title, what is true when it is done, and its status."""
        if card is None:
            return
        item.update(card=card["id"], card_title=card["title"], card_statement=card.get("statement", ""),
                    card_status=card.get("status", "open"))

    def _task_zones(self, world: Dict[str, Any], task: str) -> List[Dict[str, Any]]:
        """A task's places: its own, its parts, the scenes it is grouped into - not its ground."""
        return [z for z in world["zones"] if (not z.get("plot") or z.get("great"))
                and (str(z.get("task") or "") == task or z.get("part_of") == task or task in (z.get("tasks") or []))]

    def _room_cards(self, world: Dict[str, Any], rooms: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """WHICH CARDS EACH ROOM HOLDS: a place of a deliverable ("card") its card and all under it; a task's other
        rooms the rest of its tree, shared out in the tree's order - a card shown by a thing kept elsewhere left out."""
        trees = dict(world.get("work_trees") or {})
        self._card_index = {c["id"]: c for cards in trees.values() for c in cards}
        shown = {one.get("card") for one in world["items"] if one.get("card")}
        out: Dict[str, List[Dict[str, Any]]] = {}
        for task, tree in trees.items():
            places = [z for z in rooms if z in self._task_zones(world, task)]
            if not places:
                continue
            given: set = set()
            for zone in places:
                if zone.get("card"):
                    mine = muse.subtree(tree, zone["card"])
                    out[zone["id"]] = mine
                    given |= {c["id"] for c in mine}
            rest = [c for c in tree if c["id"] not in given]
            others = [z for z in places if not z.get("card")]
            if not others:
                continue
            per = -(-len(rest) // len(others))
            for n, zone in enumerate(others):
                out.setdefault(zone["id"], []).extend(rest[n * per:(n + 1) * per])
        for zone_id in list(out):
            kept_here = {one.get("card") for one in world["items"] if one["zone"] == zone_id and one.get("card")
                         and self._kept_in_room(one)}
            out[zone_id] = [c for c in out[zone_id] if c["id"] not in (shown - kept_here) or c["id"] in kept_here]
        return out

    # -- A PROJECT'S GREAT GROUND (agent/playgreat.py; the user, 2026-11-19) ------------------------------------------
    def _great_grounds(self, at: str) -> int:
        """EACH PROJECT LARGE ENOUGH ONE GREAT GROUND: its places in its main land, gathered, at least
        GREAT_AREA_LEAST tiles or GREAT_PLACES_LEAST places - designed once (_design_great), then again only when it
        has grown GREAT_REDO_GROWN times or its tasks changed. How many were designed."""
        with self._lock:
            world = self.world()
        done = 0
        for project in list(world.get("projects") or []):
            pid = str(project.get("id") or "")
            tasks = [str(one) for one in project.get("tasks") or []]
            if not pid or not tasks:
                continue
            ground = next((z for z in world["zones"] if z.get("great") == pid), None)
            mine = [z for z in world["zones"] if not z.get("great") and (
                str(z.get("task") or "") in tasks or z.get("part_of") in tasks or set(z.get("tasks") or []) & set(tasks))]
            places_ = [z for z in mine if not z.get("plot")]
            if not places_:
                continue
            main = max(places_, key=lambda z: z["w"] * z["h"])
            region = next((r for r in world["regions"] if r["id"] == (ground or main).get("region")), None)
            if region is None:
                continue
            mine = [z for z in mine if z.get("region") == region["id"]]
            places_ = [z for z in mine if not z.get("plot")]
            rects = [places.zone_rect(z) for z in mine] + ([places.zone_rect(ground)] if ground else [])
            if not rects:
                continue
            x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
            x1, y1 = max(r[2] for r in rects), max(r[3] for r in rects)
            area = (x1 - x0) * (y1 - y0)
            if ground is None and area < great.GREAT_AREA_LEAST and len(places_) < great.GREAT_PLACES_LEAST:
                continue
            kept = (world.get("great") or {}).get(pid) or {}
            with_places = sorted({str(z.get("task") or z.get("part_of") or "") for z in places_} - {""})
            if kept and self._great_progress(world, pid):
                with self._lock:                         # its stages as far as they are now
                    fresh = self.world()
                    if self._great_progress(fresh, pid):
                        self.save(fresh)
            # NEVER OVER ANOTHER'S PLACES (the user, 2026-11-20: "这里怎么把2个区域混一起了"): its places far apart,
            # the frame round them took in the places of others between them. Then a ground of its own size - its
            # places' floors and room round them - on free ground in its land, else in a land of its own (its theme
            # the same, _district); its places gathered into it as it is laid (_lay_great).
            own = {z["id"] for z in mine} | ({ground["id"]} if ground else set())
            over = [z for z in world["zones"] if z["id"] not in own and z.get("region") == region["id"]
                    and places.zone_rect(z)[0] < x1 and x0 < places.zone_rect(z)[2]
                    and places.zone_rect(z)[1] < y1 and y0 < places.zone_rect(z)[3]]
            if over:
                moved = self._great_free(world, project, main, places_, region, own)
                if moved is None:
                    log.info("no free ground for the great ground of %s", project.get("name"))
                    continue
                region, (x0, y0, x1, y1) = moved
                area = (x1 - x0) * (y1 - y0)
            elif kept and set(kept.get("tasks") or []) == set(with_places) and \
                    area < great.GREAT_REDO_GROWN * float(kept.get("area") or 0):
                continue
            if self._design_great(world, project, places_, region, (x0, y0 + 1, x1 - x0, y1 - y0 - 1), with_places, at):
                done += 1
                with self._lock:
                    world = self.world()
        return done

    def _great_free(self, world: Dict[str, Any], project: Dict[str, Any], main: Dict[str, Any],
                    places_: List[Dict[str, Any]], region: Dict[str, Any], own: set
                    ) -> Optional[Tuple[Dict[str, Any], Tuple[int, int, int, int]]]:
        """Free ground for a project's great ground, as large as its places need (GREAT_ROOM_ROUND times their
        floors, at least GREAT_AREA_LEAST): in its land, near its largest place; else in a land of its own beside the
        others, its theme the same (_district). The land and the frame (x0, y0, x1, y1), or None."""
        need = max(great.GREAT_AREA_LEAST, int(sum(z["w"] * (z["h"] + 1) for z in places_) * GREAT_ROOM_ROUND))
        widest = max(z["w"] for z in places_) + 4
        w = max(widest, math.ceil(math.sqrt(need * 1.4)))
        h = max(max(z["h"] for z in places_) + 5, math.ceil(need / w))
        others = [places.zone_rect(z) for z in world["zones"] if z["id"] not in own]
        anchor = (main["x"] + main["w"] / 2, main["y"] + main["h"] / 2)
        spot = places.find_spot(world, region, "estate", w, h, anchor, str(project.get("id")), others)
        if spot is None:
            for more in (1.5, 3, 6):
                try:
                    region = self._district(world, str(project.get("id")), main, _now(),
                                            name=str(project.get("name") or ""), area=int(w * h * more))
                except Exception:
                    log.info("no land of its own could be opened for %s", project.get("name"), exc_info=True)
                    return None
                spot = places.find_spot(world, region, "estate", w, h, places.heart(world, region),
                                        str(project.get("id")), others)
                if spot is not None:
                    break
        if spot is None:
            return None
        return region, (spot[0], spot[1], spot[0] + w, spot[1] + h)     # its footprint, round it, free too

    def _design_great(self, world: Dict[str, Any], project: Dict[str, Any], places_: List[Dict[str, Any]],
                      region: Dict[str, Any], box: Tuple[int, int, int, int], tasks: List[str], at: str) -> bool:
        """ONE PROJECT'S GREAT GROUND (agent/playgreat.py): the survey of where it stands and the human places to draw
        from (play_world_great_survey, the web searched - nothing of the project sent), its outline, its plan
        (play_world_great_plan) - then laid (_lay_great); its open ground designed after with the rooms. Whether it was."""
        left, top, w, h = box
        others = [r for r in world["regions"] if r["id"] != region["id"]]
        # A land opened for a project is named after it (_district): its name never goes to the web's survey.
        named = ("scene", "biome", "layout", "weather", "edges") if region.get("district_of") else \
            ("name", "scene", "biome", "layout", "weather", "edges")
        here = {"land": {k: region.get(k) for k in named if region.get(k)},
                "size": f"{w} x {h} tiles",
                "around": [{k: r.get(k) for k in ("scene", "biome", "edges") if r.get(k)} for r in others][:6],
                "near": sorted({z["kind"] for z in world["zones"] if z.get("region") == region["id"] and z not in places_
                                and not z.get("plot")})[:12]}
        try:
            # WHAT IS THEIRS KNOWN TO THE SEARCH'S GUARD (the user, 2026-11-20: "这一步没有加搜索检查...这个改就行"): the
            # project's name and summary, its tasks' titles, its places' names, its cards' titles - none of it in a search.
            book = world.get("tasks") or {}
            theirs = {"project": [project.get("name"), project.get("summary")],
                      "tasks": [(book.get(t) or {}).get("title") for t in tasks],
                      "places": [z.get("name") for z in places_] + [z.get("task_title") for z in places_],
                      "cards": [c.get("title") for t in tasks for c in (world.get("work_trees") or {}).get(t) or []]}
            survey = great.read_survey(self._call(great.survey_system(), great.TASK_GREAT_SURVEY,
                                                  json.dumps(here, ensure_ascii=False), 2500,
                                                  '"surroundings", "where" and "traits"', web=True,
                                                  theirs=json.dumps(theirs, ensure_ascii=False)))
        except Exception:
            log.info("the ground of %s could not be surveyed", project.get("name"), exc_info=True)
            return False
        seed = (sum(ord(ch) for ch in str(project.get("id"))) % 628) / 100
        shape = {"kind": survey["shape"], "seed": seed}
        grid = great.coarse_grid(shape, box)
        trees = world.get("work_trees") or {}
        book = world.get("tasks") or {}
        info = getattr(self, "_important_info", {}) or {}
        cards = {t: [{k: c[k] for k in ("id", "title", "status", "depth", "minutes", "tries", "reworks") if k in c}
                     for c in (trees.get(t) or [])[:GREAT_CARDS_TOLD]] for t in tasks}
        by_time = [i for t in tasks for i in muse.featured_steps(trees.get(t) or [])[:2]]
        open_kinds = [k for k in places.OUTDOOR if k in ZONE_KINDS and k not in GREAT_NOT_PARTS]
        indoor_kinds = [k for k in ZONE_KINDS if not ZONE_KINDS[k].get("outdoor") and k != "home"]
        main_zone = max(places_, key=lambda z: z["w"] * z["h"])
        muses = (world.get("muses") or {}).get(project["id"]) or {}
        ask = {"survey": survey, "grid": grid, "mode": survey["mode"],
               **({"concept": main_zone["concept"]} if main_zone.get("concept") else {}),
               **({"muses": {"design": muses.get("design")}} if muses.get("design") else {}),
               "tasks": [{"id": t, "title": (info.get(t) or {}).get("title") or next(
                   (z.get("task_title") or z.get("name") for z in places_ if z.get("task") == t), t),
                          "minutes": round(float((book.get(t) or {}).get("minutes") or 0))} for t in tasks],
               "work": cards, **({"stood_out_by_time": by_time} if by_time else {})}
        try:
            plan = great.read_plan(self._call(great.plan_system(open_kinds, indoor_kinds, muse.STAGES_MOST),
                                              great.TASK_GREAT_PLAN, json.dumps(ask, ensure_ascii=False), 6000,
                                              '"idea", "why" and "title"'),
                                   grid, tasks, {t: [c["id"] for c in cs] for t, cs in cards.items()}, survey["mode"],
                                   open_kinds, indoor_kinds)
        except Exception:
            log.info("the ground of %s could not be planned", project.get("name"), exc_info=True)
            return False
        plan["by_time"] = [{"card": i, "by": "time"} for i in by_time if i not in {f["card"] for f in plan["featured"]}][:2]
        with self._lock:
            fresh = self.world()
            fresh_places = [z for z in fresh["zones"] if z["id"] in {one["id"] for one in places_}]
            self._lay_great(fresh, project, fresh_places, region, box, shape, survey, plan, tasks, at)
            self.save(fresh)
        return True

    def _lay_great(self, world: Dict[str, Any], project: Dict[str, Any], places_: List[Dict[str, Any]],
                   region: Dict[str, Any], box: Tuple[int, int, int, int], shape: Dict[str, Any],
                   survey: Dict[str, Any], plan: Dict[str, Any], tasks: List[str], at: str) -> None:
        """THE GREAT GROUND LAID: the project's old blocks' grounds let go; one ground in its outline; each task's
        place its part as planned - open, its own outline on the ground; under a roof, a hall of the one building -
        what the plan left out placed by the code on the ground's free cells; their things kept only where they must
        be (muses, the news), the rest designed again with the ground."""
        pid = str(project["id"])
        left, top, w, h = box
        old = {z["id"] for z in world["zones"] if z.get("plot") and not z.get("great") and str(z.get("task") or "") in tasks}
        world["zones"] = [z for z in world["zones"] if z["id"] not in old]
        world["items"] = [i for i in world["items"] if i.get("zone") not in old]
        ground = next((z for z in world["zones"] if z.get("great") == pid), None)
        if ground is None:
            ground = {"id": new_zone_id(world), "kind": "estate", "plot": True, "great": pid,
                      "span": [1, 1], "style": "", "grown": [], "door": 0, "made": at}
            world["zones"].insert(0, ground)            # laid first: its parts drawn over it
        days = sorted({d for z in places_ for d in z.get("days") or []})
        ground.update(name=str(project.get("name") or ground.get("name") or "")[:60], why=plan.get("idea") or "",
                      region=region["id"], x=left, y=top - 1, w=w, h=h, shape=shape, task=tasks[0] if tasks else "",
                      tasks=list(tasks), **({"days": [days[0], days[-1]]} if days else {}))
        ground.pop("designed", None)
        world["items"] = [i for i in world["items"] if i.get("zone") != ground["id"] or self._kept_in_room(i)]
        grid = great.coarse_grid(shape, box)
        free = {(c, r) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch == "#"}
        for part in plan["parts"]:
            c, r, cw, ch = part["cell"]
            free -= {(c + dx, r + dy) for dx in range(cw) for dy in range(ch)}
        by_task = {p["task"]: p for p in plan["parts"]}
        for zone in sorted(places_, key=lambda z: -(z["w"] * z["h"])):
            task = str(zone.get("task") or zone.get("part_of") or "")
            part = by_task.pop(task, None)
            if part is None:                             # left out: on the largest free run of cells
                spot = self._free_cells(free, 3, 3) or self._free_cells(free, 2, 2)
                if spot is None:
                    continue
                part = {"task": task, "kind": zone["kind"] if not indoors(zone) else "garden", "indoor": False,
                        "cell": list(spot), "shape": "blob", "why": ""}
                free -= {(spot[0] + dx, spot[1] + dy) for dx in range(spot[2]) for dy in range(spot[3])}
            tx, ty, tw, th = great.part_rect(part, box)
            before = self.floor_of(zone)
            zone["kind"] = part["kind"]
            if part["indoor"]:
                zone.update(x=tx, y=ty, w=max(6, tw - 2), h=max(4, th - 6))
                zone.pop("shape", None)
                zone.pop("designed", None)
            else:
                zone.update(x=tx, y=ty - 1, w=max(3, tw), h=max(3, th))
                zone["shape"] = {"kind": part["shape"], "seed": (len(zone["id"]) * 1.7) % 6.283}
                zone["designed"] = at                    # designed with the ground, as one
            zone.update(region=region["id"], in_great=pid, **({"part_why": part["why"]} if part.get("why") else {}))
            zone.pop("landmark", None)
            after = self.floor_of(zone)
            dx, dy = after[0] - before[0], after[1] - before[1]
            kept = []
            for item in world["items"]:
                if item.get("zone") != zone["id"]:
                    kept.append(item)
                elif self._kept_in_room(item):           # its muse, its news: kept, moved with it
                    item["x"] += dx
                    item["y"] += dy
                    kept.append(item)
            world["items"] = kept
        self._join_halls(world, pid, box, [p for p in plan["parts"] if p["indoor"]])
        cards = [c for t in tasks for c in (world.get("work_trees") or {}).get(t) or []]
        world.setdefault("great", {})[pid] = {
            "area": w * h, "box": list(box), "tasks": list(tasks), "made": at, "mode": survey["mode"],
            "shape": shape, "survey": survey.get("surroundings", ""), "references": survey.get("references") or [],
            "idea": plan.get("idea", ""), "ways": plan.get("ways", ""),
            "stages": muse.stages_of(cards, plan.get("stages") or []) or [s for t in tasks for s in muse.stages_of(
                (world.get("work_trees") or {}).get(t) or [])][:muse.STAGES_MOST],
            "stage_cells": [s.get("cell") for s in plan.get("stages") or []],
            "stage_groups": [{"title": s["title"], "cards": s["cards"]} for s in plan.get("stages") or []],
            "featured": (plan.get("featured") or []) + (plan.get("by_time") or [])}

    def _join_halls(self, world: Dict[str, Any], pid: str, box: Tuple[int, int, int, int],
                    parts: List[Dict[str, Any]]) -> None:
        """ONE BUILDING OF ROOMS (the user, 2026-11-19: "融合之后不要独立房子"; "真正连通，允许有的连通有的不，像一个个房间"):
        a great ground's halls side by side in a row where the first stands - one top, one depth, each next one's left
        wall the last one's right - a door through the wall they share where the plan said "open_to_next"; a row too
        wide for the ground going on in a second building below. An open part a hall now stands on moved to free ground."""
        left, top, w, h = box
        order = {p["task"]: n for n, p in enumerate(parts)}
        halls = sorted((z for z in world["zones"] if z.get("in_great") == pid and indoors(z)),
                       key=lambda z: (order.get(str(z.get("task") or ""), 99), z["x"]))
        if not halls:
            return
        joins = {p["task"]: p.get("open_to_next", True) for p in parts}
        depth = min(max(z["h"] for z in halls), max(4, h - 7))
        x, y = max(left, min(z["x"] for z in halls)), max(top, min(z["y"] for z in halls))
        before = None
        for zone in halls:
            zone["inner_doors"] = []
            if before is not None and x + zone["w"] + 2 > left + w:      # too wide: the next building, below
                x, y, before = left, y + depth + 7, None
            moved = (x - zone["x"], y - zone["y"])
            zone.update(x=x, y=y, h=depth)
            for item in world["items"]:
                if item.get("zone") == zone["id"]:
                    item["x"] += moved[0]
                    item["y"] += moved[1]
            if before is not None and joins.get(str(before.get("task") or ""), True):
                # the wall they share is the last one's right: a door through it, two tall, half way down
                before["inner_doors"].append({"x": zone["x"], "y": zone["y"] + 3 + max(0, depth // 2 - 1)})
            before = zone
            x = zone["x"] + zone["w"] + 1                # the next one's left wall this one's right
        taken = set()
        for zone in halls:
            x0, y0, x1, y1 = places.zone_rect(zone)
            taken |= {(a, b) for a in range(x0, x1) for b in range(y0, y1)}
        grid = great.coarse_grid((world.get("great") or {}).get(pid, {}).get("shape") or
                                 next((z.get("shape") for z in world["zones"] if z.get("great") == pid), None), box)
        free = {(c, r) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch == "#"
                and not {(left + c * great.COARSE + dx, top + r * great.COARSE + dy)
                         for dx in range(great.COARSE) for dy in range(great.COARSE)} & taken}
        for zone in world["zones"]:
            if zone.get("in_great") != pid or indoors(zone):
                continue
            fx, fy, fw, fh = self.floor_of(zone)
            mine = {(a, b) for a in range(fx, fx + fw) for b in range(fy, fy + fh)}
            free -= {((a - left) // great.COARSE, (b - top) // great.COARSE) for a, b in mine}
            if not mine & taken:
                continue
            cw, ch = max(1, fw // great.COARSE), max(1, fh // great.COARSE)
            spot = self._free_cells(free, cw, ch) or self._free_cells(free, 2, 2)
            if spot is None:
                continue
            zone.update(x=left + spot[0] * great.COARSE, y=top + spot[1] * great.COARSE - 1,
                        w=spot[2] * great.COARSE, h=spot[3] * great.COARSE)
            free -= {(spot[0] + dx, spot[1] + dy) for dx in range(spot[2]) for dy in range(spot[3])}

    def _build_pieces(self, zone_id: str, asked: List[Dict[str, Any]], at: str) -> int:
        """EACH GREAT PIECE A PLACE ASKED FOR, BUILT (agent/playpieces.py): designed in a call of its own
        (play_world_piece) - its kit's parts - sketched and looked at once (play_world_piece_look), then laid as one
        thing on its ground, the things there let go. How many were."""
        with self._lock:
            world = self.world()
        zone = next((z for z in world["zones"] if z["id"] == zone_id), None)
        if zone is None:
            return 0
        index = {c["id"]: c for cards in (world.get("work_trees") or {}).values() for c in cards}
        region = next((r for r in world.get("regions") or [] if r["id"] == zone.get("region")), {})
        made = 0
        for one in asked:
            card = index.get(str(one.get("card") or ""))
            stage = next((s for s in ((world.get("great") or {}).get(str(zone.get("great"))) or {}).get("stages") or []
                          if s.get("n") == one.get("stage")), None)
            ask = {"what": one["what"], "w": one["w"], "h": one["h"],
                   "place": {"kind": zone["kind"], "name": zone.get("name", ""), "land": region.get("biome", ""),
                             "palette": region.get("palette", ""), "idea": zone.get("room_idea", "")},
                   "work": ({"title": card["title"], "status": card["status"]} if card else
                            {"title": stage["title"], "done": stage["done"], "of": stage["of"]} if stage else {})}
            # FROM THE LIBRARY (the user, 2026-11-20: "画过的大型控件也添加描述，作为一个之后可以复用的东西"; "都应该可以build
            # on top of, 以一个为基础进行修改这样"): one made before used again as it was - at its own size, where that fits
            # the ground asked - or built on: its picture or parts given to the design, only what is to change changed.
            library = getattr(self, "_library", None) or self._made_library()
            kept = (library.get("pieces") or {}).get(str(one.get("use") or one.get("base") or ""))
            if kept and one.get("use") and int(kept.get("w") or 0) <= one["w"] and int(kept.get("h") or 0) <= one["h"]:
                spec = dict(kept.get("spec") or {})
                if spec:
                    one = {**one, "w": int(kept["w"]), "h": int(kept["h"]), "kit": kept.get("kit") or one["kit"]}
                    self._lay_piece(zone_id, one, spec, None, at, used=str(one["use"]))
                    made += 1
                    continue
            if kept and not one.get("base"):              # asked to use one at another size: built on it at this one
                one = {**one, "base": str(one.get("use")), "change": "the same piece, at this ground's size"}
            ask_base = ({"build_on": {"name": one["base"], "what": kept.get("description", ""), "kit": kept.get("kit"),
                                      **({"picture": (kept.get("spec") or {}).get("svg")} if (kept.get("spec") or {}).get("svg")
                                         else {"parts": kept.get("spec")})},
                         "change": one.get("change", "")} if kept and one.get("base") else {})
            # DRAWN FIRST, THEN BUILT AFTER ITS PICTURE (the user, 2026-11-19: "大件是不是应该先根据模型描述，画一个图片，然后
            # 跟着图片搭"): its picture (SVG), looked at as it is drawn, kept - else, that failing, its kit's parts as before.
            spec = None
            svg_system = pieces.piece_svg_system(one["w"], one["h"]) + (pieces.BUILD_ON_NOTE if ask_base else "")
            ask.update(ask_base)
            # BUILT AFTER ONE OF THEIR PICTURES (the user, 2026-11-19: "也可以用像素风拼出某个图片（交给大item design之类)"):
            # the picture shown to its designer, its piece drawn after it.
            theirs = ((getattr(self, "_room_offer", None) or {}).get(zone_id) or {}).get(str(one.get("picture") or ""))
            photo = pieces.photo_block(theirs.get("path")) if theirs else None
            if photo is not None:
                ask["after_their_picture"] = theirs.get("what") or "a picture of their work"
                svg_system += ("\nTHEIR PICTURE IS GIVEN (\"after_their_picture\"): draw this piece AFTER IT - its shapes, "
                               "its colours, its composition rebuilt in your own pixel-style drawing, a monument of it.")
            try:
                said = self._call(svg_system, pieces.TASK_PIECE,
                                  [{"type": "text", "text": json.dumps(ask, ensure_ascii=False)},
                                   {"type": "image", "source": {"type": "base64", **photo}}]
                                  if photo is not None else json.dumps(ask, ensure_ascii=False), 16000,
                                  '"name" and "why"')
                drawn = pieces.read_svg(said, one["w"], one["h"])
                picture = pieces.render_svg(drawn["svg"], one["w"], one["h"]) if drawn else None
                if picture is not None:
                    again = self._call(pieces.PIECE_SVG_LOOK_SYSTEM + "\n\n" + svg_system, pieces.TASK_PIECE_LOOK, [
                        {"type": "text", "text": json.dumps({"your_picture": drawn["svg"], **ask}, ensure_ascii=False)},
                        {"type": "image", "source": {"type": "base64", **picture}},
                        *([{"type": "text", "text": "Their picture it is drawn after - how near is yours to it?"},
                           {"type": "image", "source": {"type": "base64", **photo}}] if photo is not None else [])],
                        16000, '"name", "why" and "looked"')
                    drawn = pieces.read_svg(again, one["w"], one["h"]) or drawn
                spec = drawn
            except Exception:
                log.info("a great piece could not be drawn", exc_info=True)
            system = pieces.piece_system(one["kit"]) + (pieces.BUILD_ON_NOTE if ask_base else "")
            try:
                if spec is not None:
                    raise StopIteration                  # drawn: no kit
                if photo is not None:
                    system += ("\nTHEIR PICTURE IS GIVEN (\"after_their_picture\"): build this piece AFTER IT - its "
                               "shapes and colours, in the kit's parts.")
                said = self._call(system, pieces.TASK_PIECE,
                                  [{"type": "text", "text": json.dumps(ask, ensure_ascii=False)},
                                   {"type": "image", "source": {"type": "base64", **photo}}]
                                  if photo is not None else json.dumps(ask, ensure_ascii=False), 4000, '"name" and "why"')
                spec = pieces.read_piece(said, one["kit"], one["w"], one["h"])
                picture = pieces.draw_piece(one["kit"], spec, one["w"], one["h"]) if spec else None
                if picture is not None:
                    again = self._call(pieces.PIECE_LOOK_SYSTEM + "\n\n" + system, pieces.TASK_PIECE_LOOK, [
                        {"type": "text", "text": json.dumps({"your_design": said, **ask}, ensure_ascii=False)},
                        {"type": "image", "source": {"type": "base64", **picture}},
                        *([{"type": "text", "text": "Their picture it is built after - how near is yours to it?"},
                           {"type": "image", "source": {"type": "base64", **photo}}] if photo is not None else [])],
                        4000, '"name", "why" and "looked"')
                    better = pieces.read_piece(again, one["kit"], one["w"], one["h"])
                    if better:
                        spec = better
            except StopIteration:
                pass
            except Exception:
                log.info("a great piece could not be designed", exc_info=True)
                continue
            if not spec:
                continue
            self._keep_piece(one, spec, at)
            self._lay_piece(zone_id, one, spec, photo, at)
            made += 1
        return made

    def _keep_piece(self, one: Dict[str, Any], spec: Dict[str, Any], at: str) -> None:
        """A GREAT PIECE KEPT IN THE LIBRARY (made.json "pieces"; the user, 2026-11-20: "画过的大型控件也添加描述，作为一个
        之后可以复用的东西"): its name, kit, size, what it is and its design - offered to every design after, to be used
        again or built on; what it was built on kept with it."""
        library = getattr(self, "_library", None) or self._made_library()
        shelf = library.setdefault("pieces", {})
        name = made_lib.name_of(spec.get("name") or one.get("what")) or f"piece_{len(shelf) + 1}"
        while name in shelf and shelf[name].get("spec") != spec:
            name = f"{name[:36]}_{len(shelf) + 1}"
        shelf[name] = {"kit": one.get("kit"), "w": one["w"], "h": one["h"], "spec": spec, "made": at,
                       "description": " ".join(str(one.get("what") or spec.get("why") or "").split())[:300],
                       **({"based_on": str(one["base"])} if one.get("base") else {}),
                       **({"station": str(one["station"])} if one.get("station") else {}),
                       **({"civic": str(one["civic"])} if one.get("civic") else {})}
        try:
            made_lib.keep_library(self.folder, library)
        except OSError:
            log.info("a great piece could not be kept in the library", exc_info=True)
        self._library = library

    def _lay_piece(self, zone_id: str, one: Dict[str, Any], spec: Dict[str, Any], photo: Any, at: str,
                   used: str = "") -> None:
        """A great piece laid on its ground as one thing, the things there let go."""
        with self._lock:
            fresh = self.world()
            tiles = {(one["x"] + dx, one["y"] + dy) for dx in range(one["w"]) for dy in range(one["h"])}
            fresh["items"] = [i for i in fresh["items"] if not (i.get("zone") == zone_id and not i.get("on")
                                                                and not i.get("hang") and self._tiles_of(i) & tiles
                                                                and not self._kept_in_room(i))]
            item = {"id": new_item_id(fresh), "zone": zone_id, "kind": "piece", "kit": one["kit"],
                    "x": one["x"], "y": one["y"], "w": one["w"], "h": one["h"], "spec": spec,
                    "name": spec.get("name") or "", "why": spec.get("why") or "", "what": one["what"], "from": "",
                    "made": at, "picture": "", "designed": True,
                    # ridden round its track, or ON ITS BOATS round its water (2026-11-20: "天鹅游船"); climbed; looked at
                    "interact": ("ride" if spec.get("track") or spec.get("boats") else "climb" if one["kit"] == "stack"
                                 else "look") if spec.get("svg") else
                    ("ride" if spec.get("boats") else pieces.INTERACT.get(one["kit"], "look")),
                    **({"stage": one["stage"]} if one.get("stage") else {}),
                    **({"used": used} if used else {}), **({"based_on": str(one["base"])} if one.get("base") else {}),
                    **({"picture_of": str(one["picture"])} if photo is not None else {})}   # not offered again
            self._mark_card(item, self._card_named(str(one.get("card") or "")) if one.get("card") else None)
            fresh["items"].append(item)
            self.save(fresh)

    #: Tries at designing a piece left without a design before its tiles are let go.
    PIECE_TRIES_MOST = 3

    def _finish_pieces(self, at: str) -> int:
        """EVERY GREAT PIECE DRAWN (the user, 2026-11-19: "你的大件要保证能画出来呀" - a lab's design had laid a "piece"
        tile by tile, with a name and no size or design, and the page could not draw it): each piece without its own
        design, its tiles in one place under one name taken together, is designed and built as a piece is
        (_build_pieces) on the ground they cover - the thing it was meant to be. Tried PIECE_TRIES_MOST times, then
        let go. How many were built."""
        with self._lock:
            world = self.world()
        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for one in world.get("items") or []:
            if one.get("kind") == "piece" and not one.get("spec"):
                groups.setdefault((str(one.get("zone")), str(one.get("name") or "")), []).append(one)
        built = 0
        for (zone_id, name), ones in groups.items():
            xs = [int(o["x"]) for o in ones] + [int(o["x"]) + max(1, int(o.get("w") or 1)) - 1 for o in ones]
            ys = [int(o["y"]) for o in ones] + [int(o["y"]) + max(1, int(o.get("h") or 1)) - 1 for o in ones]
            first = ones[0]
            ask = {"kit": first.get("kit") if first.get("kit") in pieces.KITS else "composite",
                   "what": " ".join(x for x in (name, str(first.get("why") or ""), str(first.get("what") or "")) if x)[:600],
                   "x": min(xs), "y": min(ys), "w": max(xs) - min(xs) + 1, "h": max(ys) - min(ys) + 1,
                   **({"card": str(first["card"])} if first.get("card") else {}),
                   **({"stage": first["stage"]} if isinstance(first.get("stage"), int) else {})}
            made = 0
            try:
                made = self._build_pieces(zone_id, [ask], at)
            except Exception:
                log.info("a great piece could not be finished", exc_info=True)
            built += made
            if made:
                continue
            with self._lock:                             # not this time: tried again, at most PIECE_TRIES_MOST times
                fresh = self.world()
                ids = {o["id"] for o in ones}
                left = [o for o in fresh["items"] if o.get("id") in ids]
                tries = 1 + max([int(o.get("piece_tries") or 0) for o in left] or [0])
                if tries >= self.PIECE_TRIES_MOST:
                    fresh["items"] = [o for o in fresh["items"] if o.get("id") not in ids]
                else:
                    for o in left:
                        o["piece_tries"] = tries
                self.save(fresh)
        return built

    def cards_now(self, world: Dict[str, Any]) -> bool:
        """THE WORK AS IT IS NOW, FOR THE PAGE (a side agent, 2026-11-19: what the widgets and great pieces showed was the
        world's copy of the cards, brought up to date only when the world grew - a card done today ripened tomorrow):
        each card's status, time and tries read again from the Work panel (read only, nothing asked of a model), each
        great ground's stages again from them - into the world given, which is sent, not kept. Whether any changed."""
        trees = world.get("work_trees") or {}
        if not trees or not callable(self.cards):
            return False
        panel = dict(self.cards(list(trees)) or {})
        changed = False
        for task, cards in trees.items():
            now = {c["id"]: c for c in panel.get(task) or []}
            for card in cards:
                fresh = now.get(card["id"])
                if fresh is None:
                    continue
                for key in ("status", "minutes", "tries", "reworks", "result"):
                    if key in fresh and card.get(key) != fresh[key]:
                        card[key] = fresh[key]
                        changed = True
        index = {c["id"]: c for cards in trees.values() for c in cards}
        for one in world.get("items") or []:              # a card's thing: its status as it is
            card = index.get(one.get("card")) if one.get("card") else None
            if card is not None and one.get("card_status") != card.get("status"):
                one["card_status"] = card.get("status")
                changed = True
        for pid in list((world.get("great") or {})):
            changed = self._great_progress(world, pid) or changed
        return changed

    # -- WHAT ROOMS ARE MADE OF, AND WHAT THE AGENT MAKES FOR THEM (agent/playmade.py) -------------------------------
    def _made_library(self) -> Dict[str, Any]:
        """The library of things and floors made before (made.json beside the world), each made thing a kind."""
        library = made_lib.load_library(self.folder)
        for name, one in (library.get("items") or {}).items():
            if name not in ITEM_KINDS and isinstance(one.get("size"), list):
                ITEM_KINDS[name] = {"size": tuple(one["size"]), "made": True}
        return library

    def made(self) -> Dict[str, Any]:
        """The library, for the page."""
        return made_lib.load_library(self.folder)

    def _make_asked(self, said: Any, zone: Dict[str, Any], land_look: str) -> int:
        """WHAT A ROOM'S DESIGN ASKED TO BE MADE, MADE (play_world_make, one call for all of it): drawn, kept to what is
        safe, kept in the library for every room after, each thing a kind at once - so the design's use of it stands.
        How many were."""
        library = getattr(self, "_library", None) or self._made_library()
        floors = set(made_lib.materials().get("floors") or {}) | set(library.get("floors") or {}) | \
            set(made_lib.materials().get("walls") or {}) | set(library.get("walls") or {})
        asked = made_lib.read_asked(said, library, lambda name: name in ITEM_KINDS or name in floors)
        if not asked:
            return 0
        try:
            bases = made_lib.bases_of(asked, library)
            drawn = self._call(made_lib.MAKE_SYSTEM, made_lib.TASK_MAKE, json.dumps(
                {"make": asked, "land_look": land_look, "room": {"kind": zone["kind"], "name": zone.get("name", ""),
                                                                  "idea": zone.get("room_idea", "")},
                 **({"bases": bases} if bases else {})},
                ensure_ascii=False), 16000, '"description"')
        except Exception:
            log.info("what a room asked for could not be made", exc_info=True)
            return 0
        found = made_lib.read_made(drawn, asked, _now())
        if not found:
            return 0
        for name, one in found.items():
            if one["kind"] == "thing":
                library.setdefault("items", {})[name] = {k: one[k] for k in ("description", "size", "svg", "made", "based_on")
                                                         if k in one}
                ITEM_KINDS[name] = {"size": tuple(one["size"]), "made": True}
            elif one["kind"] == "wall":                  # a wall's face, laid over its set (play.js playWallTile)
                library.setdefault("walls", {})[name] = {k: one[k] for k in ("description", "svg", "made", "set", "based_on")
                                                         if k in one}
            else:
                library.setdefault("floors", {})[name] = {k: one[k] for k in ("description", "svg", "made", "based_on")
                                                          if k in one}
        made_lib.keep_library(self.folder, library)
        self._library = library
        return len(found)

    def _surfaces(self, zone: Dict[str, Any], said: Any) -> None:
        """A room's floor and walls as its design chose them - of the materials there are, or made."""
        if not isinstance(said, dict):
            return
        library = getattr(self, "_library", None) or {}
        floors = set(made_lib.materials().get("floors") or {}) | set(library.get("floors") or {})
        # Their own keys: "floor" is the room's grid (2026-11-19 - a material named "floor" was lost under it, every time).
        floor = made_lib.name_of(said.get("floor_material"))
        walls = made_lib.name_of(said.get("wall_material") or said.get("walls"))
        if floor in floors:
            zone["floor_material"] = floor
        if walls in (set(made_lib.materials().get("walls") or {}) | set(library.get("walls") or {})) and indoors(zone):
            zone["wall_material"] = walls

    @staticmethod
    def _great_progress(world: Dict[str, Any], pid: str) -> bool:
        """A GREAT GROUND'S STAGES AS FAR AS THEY ARE (the user: "progress (第一阶段成果，第二阶段成果)"): each stage's share of
        its cards done, its status and its result, from the cards as they are now - nothing asked. Whether any changed."""
        kept = (world.get("great") or {}).get(pid)
        if not kept or not kept.get("stage_groups"):
            return False
        cards = [c for t in kept.get("tasks") or [] for c in (world.get("work_trees") or {}).get(t) or []]
        now = muse.stages_of(cards, kept["stage_groups"])
        if now == kept.get("stages"):
            return False
        kept["stages"] = now
        return True

    @staticmethod
    def _free_cells(free: set, w: int, h: int) -> Optional[Tuple[int, int, int, int]]:
        for c, r in sorted(free, key=lambda one: (one[1], one[0])):
            if all((c + dx, r + dy) in free for dx in range(w) for dy in range(h)):
                return c, r, w, h
        return None

    def _great_brief(self, world: Dict[str, Any], zone: Dict[str, Any]) -> Dict[str, Any]:
        """What the design of a great ground's open ground is given of its plan: its idea, the survey and the places it
        draws from, its parts where they lie, its stages with how far each is and what it made, its featured steps."""
        kept = (world.get("great") or {}).get(str(zone.get("great"))) or {}
        index = {c["id"]: c for cards in (world.get("work_trees") or {}).values() for c in cards}
        left, top = zone["x"], zone["y"] + 1
        parts = []
        for z in world["zones"]:
            if z.get("in_great") == zone["great"]:
                x0, y0, x1, y1 = places.zone_rect(z)
                parts.append({"name": z.get("name", ""), "kind": z["kind"], "under_a_roof": indoors(z),
                              "where": [x0 - left, y0 - top, x1 - x0, y1 - y0]})
        return {"idea": kept.get("idea", ""), "mode": kept.get("mode", ""), "surroundings": kept.get("survey", ""),
                "references": [{k: r[k] for k in ("name", "traits") if r.get(k)} for r in kept.get("references") or []],
                "ways": kept.get("ways", ""), "parts": parts,
                "stages": [{k: s[k] for k in ("n", "title", "done", "of", "status", "result") if k in s}
                           for s in kept.get("stages") or []],
                "featured": [{"card": f["card"], "title": (index.get(f["card"]) or {}).get("title", ""),
                              "why": f.get("why", ""), "by": f.get("by", "")} for f in kept.get("featured") or []]}

    # -- THE WORLD THEN (the user, 2026-11-19: "每个task ... 也可以加上在你做这个project的时候，世界上发生了什么大事的content";
    # "不一定是报亭，也可以是作为element放在房间里") ------------------------------------------------------------------------
    def _world_events(self, at: str) -> int:
        """EACH MONTH'S GREAT EVENTS, ASKED ONCE (play_world_events, searching the web): the months the world's places
        were lived in, not yet known - this month's asked again once it is over; a month that came back empty asked
        again, twice at most. Only the months are sent, nothing of the user. Kept in world["world_events"]. How many
        months were found."""
        with self._lock:
            world = self.world()
        known = world.get("world_events") or {}
        now = at[:7]
        months = set()
        for zone in world["zones"]:
            days = sorted(str(d)[:10] for d in zone.get("days") or [] if str(d)[:4].isdigit())
            if days and (zone.get("task") or zone.get("tasks")):
                months.update(muse.months_between(days[0], days[-1]))
        wanted = [m for m in sorted(months, reverse=True) if m <= now and (
            m not in known or (not known[m].get("whole") and known[m].get("asked", "")[:7] < now)
            or (not known[m].get("events") and int(known[m].get("tries") or 0) < 2))]
        found = 0
        for n in range(0, min(len(wanted), muse.WORLD_MONTHS_A_CALL * muse.WORLD_EVENT_CALLS_MOST), muse.WORLD_MONTHS_A_CALL):
            batch = wanted[n:n + muse.WORLD_MONTHS_A_CALL]
            try:
                said = self._call(muse.WORLD_EVENTS_SYSTEM.format(most=muse.WORLD_EVENTS_A_MONTH,
                                                                  outlets=", ".join(muse.MAINSTREAM_NEWS[:20])),
                                  muse.TASK_WORLD_EVENTS,
                                  json.dumps({"months": batch}), 4000, '"headline" and "what"', web=True)
            except Exception:
                log.info("the world's events of %s could not be found", ", ".join(batch), exc_info=True)
                continue
            read = muse.read_world_events(said, batch)
            with self._lock:
                fresh = self.world()
                book = fresh.setdefault("world_events", {})
                for month in batch:
                    was = book.get(month) or {}
                    book[month] = {"events": read.get(month) or was.get("events") or [], "asked": at,
                                   "whole": month < now, "tries": int(was.get("tries") or 0) + 1}
                    found += 1 if read.get(month) else 0
                self.save(fresh)
        return found

    @staticmethod
    def _news_of(world: Dict[str, Any], zone: Dict[str, Any]) -> List[Dict[str, str]]:
        """What happened in the world while this place's work was done: from its first day to its last."""
        days = sorted(str(d)[:10] for d in zone.get("days") or [] if str(d)[:4].isdigit())
        if not days or not (zone.get("task") or zone.get("tasks")):
            return []
        return muse.news_between(world.get("world_events") or {}, days[0], days[-1], NEWS_A_PLACE)

    @staticmethod
    def _carry_news(item: Dict[str, Any], news: List[Dict[str, str]]) -> None:
        """A thing showing the world then: its headlines, each by its date and where it was reported."""
        item["news"] = [{k: e[k] for k in ("date", "headline", "url") if e.get(k)} for e in news]
        text = " · ".join(f"{e['date'][5:]} {e['headline']}" for e in news)
        if item.get("news_text") != text:
            item["news_text"] = text
            words = item.get("words")
            if isinstance(words, dict):                      # its headlines changed: put into the language again
                for said in words.values():
                    if isinstance(said, dict):
                        said.pop("news_text", None)

    def _place_news(self, world: Dict[str, Any], at: str) -> int:
        """EVERY TASK'S PLACE SHOWS THE WORLD THEN: the thing its design chose for it brought up to date; a place
        whose design chose none (or was designed before) given one - a notice of the day's news. How many changed."""
        changed = 0
        for zone in world["zones"]:
            news = self._news_of(world, zone)
            if not news:
                continue
            mine = [one for one in world["items"] if one["zone"] == zone["id"] and one.get("news")]
            if mine:
                for one in mine:
                    before = one.get("news_text")
                    self._carry_news(one, news)
                    changed += 1 if one.get("news_text") != before else 0
                continue
            made = self._add_item(world, zone, {"kind": NEWS_KIND, "name": "The world then", "from": "the news",
                                                "why": "What the world was talking about while this work was done."}, at)
            if made is not None:
                self._carry_news(made, news)
                changed += 1
        return changed

    def _cover_cards(self, world: Dict[str, Any], at: str) -> int:
        """EVERY CARD SHOWN (the code's guarantee, whatever the designs did): each card of a task's tree that no thing
        stands for given one in the task's places - a thing of its kind of work (CARD_THINGS) - and every thing's card
        status as the card's is now. How many were given one."""
        trees = dict(world.get("work_trees") or {})
        cards = {c["id"]: c for tree in trees.values() for c in tree}
        changed = 0
        for one in world["items"]:                      # their status as it is now
            card = cards.get(one.get("card") or "")
            if card is not None and (one.get("card_status"), one.get("card_title")) != (card["status"], card["title"]):
                self._mark_card(one, card)
                changed += 1
        shown = {one.get("card") for one in world["items"] if one.get("card")}
        laid = 0
        for task, tree in trees.items():
            places = self._task_zones(world, task)
            if not places:
                continue
            for n, card in enumerate(c for c in tree if c["id"] not in shown):
                kind = muse.CARD_THINGS.get(card.get("type") or "", "crate" if card["status"] == "set_aside" else "desk")
                if kind not in ITEM_KINDS:
                    kind = "desk"
                home = next((z for z in places if z.get("card") == card["id"]), None) or places[n % len(places)]
                item = self._add_item(world, home, {"kind": kind, "name": card["title"][:60],
                                                    "why": card.get("statement", "")[:300], "from": "its work"}, at)
                if item is None:
                    for other in places:                 # full: another of its places
                        item = self._add_item(world, other, {"kind": kind, "name": card["title"][:60],
                                                             "why": card.get("statement", "")[:300], "from": "its work"}, at)
                        if item is not None:
                            break
                if item is not None:
                    self._mark_card(item, card)
                    item["stand_in"] = True               # till its room's next design takes it (_design_rooms)
                    shown.add(card["id"])
                    laid += 1
        return laid + changed

    def _design_transport(self, at: str) -> bool:
        """THE WAYS BETWEEN THE LANDS, A LAYER OF THEIR OWN (playmuse.TRANSPORT_SYSTEM; the user, 2026-11-19), designed
        after the lands and their places - again only when the lands or how they are tied changed. Kept in
        world["transport"]; the page lays its lines and gates from it. True when designed now."""
        with self._lock:
            world = self.world()
        lands = [r for r in world.get("regions") or [] if not r.get("closed") and not r.get("apart")]
        ties = list(world.get("commutes") or [])
        local = self._local_lines(world, lands)
        if len(lands) < 2:
            # ONE LAND, ITS OWN LINES ALL THE SAME (the user, 2026-11-20: "还有第3个条件是地图大小太大就会要加上交通工具"):
            # nothing between lands to design, but a land grown large is ridden across (_local_lines).
            if (world.get("transport") or {}).get("local") != local:
                with self._lock:
                    fresh = self.world()
                    fresh.setdefault("transport", {})["local"] = local
                    self.save(fresh)
                return bool(local)
            return False
        signature = json.dumps([[r["id"], r.get("scene") or r.get("biome")] for r in lands]
                               + [[t["from"], t["to"], round(float(t.get("tie") or 0), 1)] for t in ties] + ["v2"])
        if (world.get("transport") or {}).get("for") == signature:
            if (world.get("transport") or {}).get("local") != local:
                with self._lock:                         # a project's far places: their local lines as they are now
                    fresh = self.world()
                    fresh.setdefault("transport", {})["local"] = local
                    self.save(fresh)
            return False
        shown = [{"id": r["id"], "name": r.get("name", ""), "scene": r.get("scene") or "", "biome": r.get("biome", ""),
                  "edges": r.get("edges") or [], "layout": r.get("layout", ""),
                  "places": [z.get("name", "") for z in world["zones"] if z.get("region") == r["id"] and not z.get("plot")][:8]}
                 for r in lands]
        try:
            said = self._call(muse.transport_system(), muse.TASK_TRANSPORT,
                              json.dumps({"lands": shown, "ties": [{k: t.get(k) for k in ("from", "to", "tie", "days")}
                                                                    for t in ties],
                                          "ways_now": muse.ways_now(world.get("transport"))}, ensure_ascii=False),
                              3000, '"name" and "why"')
        except Exception:
            log.info("the world's ways could not be designed", exc_info=True)
            said = {}
        ways = muse.read_transport(said, lands, ties, world.get("transport"))   # added to; a few dropped at most
        with self._lock:
            fresh = self.world()
            fresh["transport"] = {**ways, "local": local, "for": signature, "made": at}
            self.save(fresh)
        try:
            self._stations(at)
        except Exception:
            log.info("the lines' stations could not be built", exc_info=True)
        return True

    def _aesthetics(self, at: str) -> bool:
        """THE WORLD'S LOOKS AS A WHOLE (muse.AESTHETICS_SYSTEM; the user, 2026-11-20): after its ways, one call - where
        two lands meet and how wide, each road's edge and what stands beside it, each land's night - again only when
        the lands, their joins or the roads' kinds changed. Kept: joins with the ways (world["transport"]["joins"]),
        roads' edges in world["road_looks"], each land's night on it ("lights", "night"). Whether designed now."""
        with self._lock:
            world = self.world()
        lands = [r for r in world.get("regions") or [] if not r.get("closed")]
        if not lands:
            return False
        transport = world.get("transport") or {}
        materials = sorted({str((r.get("road") or {}).get("material") or "") for r in lands} - {""}
                           | {str(one.get("material")) for one in transport.get("roads") or [] if one.get("material")}
                           | {"dirt", "paving"})
        pairs = [{"from": a["id"], "to": b["id"]} for n, a in enumerate(lands) for b in lands[n + 1:]][:30]
        signature = json.dumps([[r["id"], r.get("scene") or r.get("biome")] for r in lands] + materials + ["v1"])
        if (world.get("looks_for") or "") == signature:
            return False
        shown = {"lands": [{"id": r["id"], "scene": r.get("scene") or "", "biome": r.get("biome", ""),
                            "look": str(r.get("look") or "")[:300], "edges": r.get("edges") or []} for r in lands],
                 "pairs": pairs, "joins_now": [{k: j.get(k) for k in ("from", "to", "how")} for j in transport.get("joins") or []],
                 "road_materials": materials}
        try:
            said = self._call(muse.aesthetics_system(), muse.TASK_AESTHETICS, json.dumps(shown, ensure_ascii=False), 3000,
                              '"why"')
        except Exception:
            log.info("the world's looks could not be designed", exc_info=True)
            return False
        design = muse.read_aesthetics(said, lands, materials)
        with self._lock:
            fresh = self.world()
            tr = fresh.setdefault("transport", {})
            joins = [j for j in tr.get("joins") or [] if not any({j["from"], j["to"]} == {d["from"], d["to"]}
                                                                for d in design["joins"])]
            tr["joins"] = joins + design["joins"]
            fresh["road_looks"] = design["roads"]
            for region in fresh.get("regions") or []:
                night = design["night"].get(str(region["id"]))
                if night:
                    region["lights"] = night["light"]
                    region["night"] = {k: night[k] for k in ("colour", "brightness", "reach", "windows", "lamps_every")}
            fresh["looks_for"] = signature
            self.save(fresh)
        log.info("the world's looks designed: %d joins, %d roads, %d nights", len(design["joins"]), len(design["roads"]),
                 len(design["night"]))
        return True

    #: A CITY'S OWN BUILDINGS, each its size in tiles (the user, 2026-11-20: "还要加城市主题，里面有常见建筑，包括商场，公寓，
    #: 摩天大楼，医院，警察局，消防局等等").
    CIVIC = {"mall": ("shopping mall", (8, 5)), "apartments": ("block of apartments", (5, 5)),
             "skyscraper": ("skyscraper", (4, 4)), "hospital": ("hospital", (7, 5)),
             "police": ("police station", (5, 4)), "fire": ("fire station", (5, 4))}

    def _civic(self, at: str) -> int:
        """EVERY CITY ITS OWN BUILDINGS (CIVIC): in each land of the city (its biome), ground for each kind where there is
        room, and the building designed as a great piece in the land's look - or one of its kind from the library used
        again as it was (no call), so a world's cities share their buildings. Each looked at as any piece (its card:
        what it is). How many were made."""
        with self._lock:
            world = self.world()
        made = 0
        for region in [r for r in world.get("regions") or [] if r.get("biome") == "city" and not r.get("closed")]:
            for key, (words, (sw, sh)) in self.CIVIC.items():
                mark = f"{region['id']}:{key}"
                with self._lock:
                    fresh = self.world()
                    if any(z.get("civic_of") == mark for z in fresh["zones"]):
                        continue
                    name = f"{words.title()}"[:60]
                    zone = self._add_zone(fresh, "civic", name, f"The city's {words}.", at, region=region)
                    if zone is None or not self._resize(fresh, zone, sw, sh):
                        if zone is not None:
                            fresh["zones"] = [z for z in fresh["zones"] if z is not zone]
                        continue
                    zone.update(civic_of=mark, designed=at)
                    self.save(fresh)
                library = getattr(self, "_library", None) or self._made_library()
                kept = next((n for n, one in (library.get("pieces") or {}).items() if one.get("civic") == key), "")
                ask = {"kit": "composite", "x": zone["x"], "y": zone["y"] + 1, "w": sw, "h": sh, "civic": key,
                       "what": (f"A {words} of a city of today - {region.get('look') or region.get('name') or ''}: "
                                f"unmistakable as what it is from afar, its sign, its doors, its windows.")[:600],
                       **({"use": kept} if kept else {})}
                try:
                    made += self._build_pieces(zone["id"], [ask], at)
                except Exception:
                    log.info("a city's building could not be built", exc_info=True)
        if made:
            log.info("a city's own buildings built: %d", made)
        return made

    #: EACH KIND OF STATION, ITS SIZE IN TILES (the user, 2026-11-20: "大的火车站，地铁站，飞机场之类。（好几格的那种）").
    STATION_OF = {"rails": ("station", (8, 4)), "metro": ("metro station", (4, 3)), "water": ("ferry terminal", (6, 3)),
                  "sky": ("sky port", (6, 4)), "air": ("airport", (12, 6)), "road": ("bus station", (4, 3))}

    def _stations(self, at: str) -> int:
        """EVERY LINE'S STOP A STATION OF ITS OWN (2026-11-20): in each land a line stops in, open ground for its kind of
        station (STATION_OF: a station for the rails, a metro station, a ferry terminal, a sky port, an airport, a bus
        station) where there is room by the land's heart, and its building designed as a great piece in the land's own
        look - BUILT ON a station of its kind made before when the library has one (the same hand, this land's look), so
        the stations of one world read as one network. Each says the lines that stop there. How many were made."""
        with self._lock:
            world = self.world()
        lines = (world.get("transport") or {}).get("lines") or []
        regions_by = {r["id"]: r for r in world.get("regions") or []}
        made = 0
        for n, line in enumerate(lines):
            vehicle = str(line.get("vehicle") or "")
            runs = "metro" if vehicle == "metro" else str((muse.VEHICLES.get(vehicle) or {}).get("runs") or "")
            if runs not in self.STATION_OF:
                continue
            words, (sw, sh) = self.STATION_OF[runs]
            for land_id in line.get("stops") or []:
                key = f"{land_id}:{runs}"
                with self._lock:
                    fresh = self.world()
                    have = next((z for z in fresh["zones"] if z.get("terminal_of") == key), None)
                    if have is not None:                 # one there already: this line stops at it too
                        if str(line.get("name") or f"L{n + 1}") not in (have.get("lines") or []):
                            have.setdefault("lines", []).append(str(line.get("name") or f"L{n + 1}"))
                            self.save(fresh)
                        continue
                    region = regions_by.get(land_id)
                    if region is None:
                        continue
                    name = f"{region.get('name') or 'The'} {words.title()}"[:60]
                    zone = self._add_zone(fresh, "terminal", name, f"Where the {vehicle} stops.", at, region=region,
                                          towards=places.heart(fresh, region))
                    if zone is None or not self._resize(fresh, zone, sw, sh):
                        if zone is not None:
                            fresh["zones"] = [z for z in fresh["zones"] if z is not zone]
                        continue
                    zone.update(terminal_of=key, vehicle=vehicle, lines=[str(line.get("name") or f"L{n + 1}")],
                                designed=at)
                    self.save(fresh)
                library = getattr(self, "_library", None) or self._made_library()
                base = next((name_ for name_, one in (library.get("pieces") or {}).items()
                             if one.get("station") == runs), "")
                fx, fy = zone["x"], zone["y"] + 1                # its floor (an open place's)
                ask = {"kit": "composite", "x": fx, "y": fy, "w": sw, "h": sh, "station": runs,
                       "what": (f"A {words} for the {vehicle} in {region.get('name') or 'this land'} - "
                                f"{region.get('look') or region.get('biome') or ''}: great and unmistakable from afar, "
                                f"its platforms or its gates where the {vehicle} comes in, the name of its lines shown "
                                f"by colour and sign.")[:600],
                       **({"base": base, "change": f"in this land's own look: {region.get('look') or region.get('biome')}"}
                          if base else {})}
                try:
                    made += self._build_pieces(zone["id"], [ask], at)
                except Exception:
                    log.info("a station could not be built", exc_info=True)
        if made:
            log.info("stations built for the lines: %d", made)
        return made

    def _local_lines(self, world: Dict[str, Any], lands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Each project's far places in each land joined by a local line (playmuse.local_lines) - and A LAND GROWN
        LARGE ridden across (the user, 2026-11-20: "地图大小太大就会要加上交通工具"): its places farther apart than
        LOCAL_FAR, a line through all of them. The code's, each growth."""
        out: List[Dict[str, Any]] = []
        for land in lands:
            for project in world.get("projects") or []:
                tasks = set(project.get("tasks") or [])
                mine = [z for z in world["zones"] if z.get("region") == land["id"] and not z.get("plot")
                        and (str(z.get("task") or "") in tasks or z.get("part_of") in tasks)]
                out += muse.local_lines(mine, land)
            every = [z for z in world["zones"] if z.get("region") == land["id"] and not z.get("plot")
                     and not z.get("in_great") and not z.get("grounds_of")]
            out += [{**line, "whole_land": True} for line in muse.local_lines(every, land)]
        return out

    def _shown_language(self) -> str:
        """The language the world is shown in: Play's own, chosen in its panel ("play_language") - else the user's
        display language. "zh" or "en" ("" for another)."""
        from ..common.languages import display_language
        try:
            code = str(self.config.get("play_language", "") or "").lower() or str(display_language(self.config) or "").lower()
        except Exception:
            return ""
        return "zh" if code.startswith("zh") else "en" if code.startswith("en") else ""

    @staticmethod
    def _language_of(text: str) -> str:
        """The language a piece of the world's words is in: Chinese if any of it is."""
        return "zh" if re.search(r"[㐀-鿿]", str(text or "")) else "en"

    @staticmethod
    def _not_in(text: str, code: str) -> bool:
        """Whether a piece of the world's words holds any of another language than `code`: for Chinese, a word of
        Latin letters ("WeChat with ..." half English; a proper noun kept by the model is asked once, then kept);
        for English, any Chinese. (Not "language": common/output_languages.py reads a function taking one as a model
        call passing its language on.)"""
        text = str(text or "")
        if code == "zh":
            return bool(re.search(r"[A-Za-z]{2,}", text))
        return bool(re.search(r"[㐀-鿿]", text))

    def translate_all(self, on_round: Optional[Callable[[], Any]] = None) -> int:
        """EVERY WORD IN THE LANGUAGE SHOWN, ROUND AFTER ROUND (_translate) - each growth, and as soon as the language is
        changed in settings; `on_round` told after each round that put some in (the page shows them as they come).
        How many were put in it."""
        done = 0
        for _round in range(TRANSLATE_ROUNDS_MOST):
            now = self._translate()
            if not now:
                break
            done += now
            if callable(on_round):
                try:
                    on_round()
                except Exception:
                    log.debug("the page could not be told of the world's words", exc_info=True)
        return done

    def translate_one(self, key: str) -> Dict[str, Any]:
        """ONE THING PUT INTO THE LANGUAGE SHOWN NOW (the user, 2026-11-19: "你play切换语言的时候，你look生成的话也要换语言"):
        looked at before the rounds reached it - its words asked for at once (_translate, that one only). Its words in
        that language, and the language."""
        target = self._shown_language()
        if target:
            self._translate(only={str(key)})
        with self._lock:
            one = dict(self._word_holders(self.world())).get(str(key)) or {}
        return {"language": target, "words": ((one.get("words") or {}).get(target) or {})}

    def _translate(self, only: Optional[set] = None) -> int:
        """IN THE LANGUAGE CHOSEN, AND KEPT (the user, 2026-11-19: "先不用中英文，就先default设置为user的display languagde
        setting。但是如果用户一开始英文，设置了切换为中文，会开始逐个翻译，直到所有都变成中文（翻译的结果保留，这样之后就可以切换）"):
        everything is written in the display language; the words of the places, things, lands, lines and projects in
        another - the newest first, TRANSLATE_MOST at a time - put into the language shown by the model
        (play_world_translate), kept beside what was written ("words": {"en": ..., "zh": ...}), so a later change back
        needs nothing asked. What was changed while it was asked is left for the next time. How many were."""
        target = self._shown_language()
        if not target:
            return 0
        with self._lock:
            world = self.world()
        wanted: Dict[str, Dict[str, str]] = {}
        for key, one in self._word_holders(world):
            if only is not None and key not in only:
                continue
            words = one.get("words") or {}
            have = words.get(target) or {}
            # Put in it under an older rule (a project's name kept whole, 2026-11-19): asked again, once, where what
            # was put in still holds the other language's words.
            old = int(words.get("rule") or 0) < TRANSLATE_RULE
            need = {f: v for f, v in self._words_of(one).items() if self._not_in(v, target)
                    and (not have.get(f) or (old and self._not_in(have[f], target)))}
            if need:
                wanted[key] = need
            if len(wanted) >= TRANSLATE_MOST:
                break
        if not wanted:
            return 0
        try:
            said = self._call(TRANSLATE_SYSTEM.replace("@LANGUAGE@", TRANSLATE_LANGUAGES[target]), TASK_TRANSLATE,
                              json.dumps(wanted, ensure_ascii=False), 10000, "nothing")
        except Exception:
            log.info("the world's words could not be put in the language shown", exc_info=True)
            return 0
        done = 0
        with self._lock:
            world = self.world()
            holders = dict(self._word_holders(world))
            for key, need in wanted.items():
                one = holders.get(key)
                given = said.get(key) if isinstance(said.get(key), dict) else None
                now = self._words_of(one) if one is not None else {}
                if given is None or any(now.get(f) != v for f, v in need.items()):
                    continue                             # changed while it was asked: the next time
                put = {f: " ".join(str(given.get(f) or "").split())[:WORD_MOST.get(f, 500)]
                       for f in need if str(given.get(f) or "").strip()}
                if set(put) != set(need):
                    continue
                words = one.setdefault("words", {})
                words.setdefault(target, {}).update(put)
                for f, v in need.items():                # what was written, kept in its own language - if it is one
                    for code in TRANSLATE_LANGUAGES:
                        if code != target and not self._not_in(v, code):
                            words.setdefault(code, {})[f] = v
                words["rule"] = TRANSLATE_RULE
                done += 1
            if done:
                self.save(world)
        return done

    @staticmethod
    def _word_holders(world: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
        """Everything in the world with words to be read, by its key - the newest of each kind first: places, lands,
        projects, the lines of its transport, things."""
        out: List[Tuple[str, Dict[str, Any]]] = []
        for key in ("zones", "regions", "projects"):
            out += [(f"{key}:{one.get('id')}", one) for one in reversed(world.get(key) or [])]
        out += [(f"transport:{n}", one) for n, one in enumerate((world.get("transport") or {}).get("lines") or [])
                if isinstance(one, dict)]
        out += [(f"items:{one.get('id')}", one) for one in reversed(world.get("items") or [])]
        return out

    @staticmethod
    def _words_of(one: Dict[str, Any]) -> Dict[str, str]:
        """What of a place, a thing, a land or a project is read: its name, its why, its room's idea, its concept's
        idea, its muses' design."""
        fields = {f: str(one[f]) for f in ("name", "why", "room_idea", "grounds_name", "summary", "floor_text", "card_title",
                                           "news_text", "feeling", "to_them", "look", "task_note", "fun_fact")
                  if isinstance(one.get(f), str) and one[f].strip()}
        if isinstance(one.get("concept"), dict) and str(one["concept"].get("idea") or "").strip():
            fields["idea"] = str(one["concept"]["idea"])
        if isinstance(one.get("muse"), dict) and str(one["muse"].get("design") or "").strip():
            fields["design"] = str(one["muse"]["design"])
        if "zone" in one and one.get("kind") and "name" not in fields:
            # A THING WITH NO NAME IS CALLED BY ITS KIND (the user, 2026-11-19: "lamp, bed这些也要翻译中文" - its card read
            # "lamp"): "autumnTree" read as "Autumn tree", put into the language shown as any name.
            kind = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(one["kind"])).replace("_", " ").strip().lower()
            if kind:
                fields["name"] = kind[0].upper() + kind[1:]
        return fields

    #: A room's design is given this many of their pictures at most; of their sources; pictures found in one growth.
    #: NO LIMIT TO THE PICTURES LOOKED FOR (the user, 2026-11-19: "没有上限，你可以找多少都行") - a thing's tried again in
    #: the growths after, FIND_TRIES_MOST times in all.
    ROOM_PICTURES_MOST, ROOM_SOURCES_MOST, FIND_TRIES_MOST = 6, 10, 3

    @staticmethod
    def _ask_to_find(item: Dict[str, Any], zone: Dict[str, Any], look_for: str) -> None:
        """A thing's picture to be looked for (_find_pictures) - kept on the thing, so a growth cut short or a search
        that found nothing is taken up again by the next."""
        item["look_for"] = " ".join(str(look_for).split())[:200]

    def _find_soon(self, at: str) -> None:
        """THE PICTURES LOOKED FOR NOW, BESIDE THE WORK STILL GOING (the user, 2026-11-19: "为什么还是没有图片" - they waited
        for every room of a growth): one finder at a time, in the background, going round again while more are asked."""
        lock = self.__dict__.setdefault("_finder_lock", threading.Lock())
        with lock:
            finder = getattr(self, "_finder", None)
            if finder is not None and finder.is_alive():
                self._find_again = True
                return
            self._find_again = False

            def run() -> None:
                while True:
                    try:
                        self._find_pictures(at)
                    except Exception:
                        log.info("the pictures asked for could not be found", exc_info=True)
                    with lock:
                        if not self._find_again:
                            self._finder = None
                            return
                        self._find_again = False
            self._finder = threading.Thread(target=run, name="play-world-find", daemon=True)
            self._finder.start()

    def _find_pictures(self, at: str) -> int:
        """EACH PICTURE ASKED FOR, FOUND (agent/playfind.py; the user, 2026-11-19: "程序不一定要看已有截图，而是要去找自己觉得
        合适的"): the model's choices, the best first - THEIR FILES ON THEIR COMPUTER NOW (the user, 2026-11-20: "你应该让他
        支持找文件，从文件里截图之类"; found by the card's words in the local index), their screenshots, the files and
        pages they had open, the web searched in more than one way ("不一定要一个途径") - each tried in turn till one
        is read and is not one shown already (2026-11-20: one page's picture was on nine things). Kept whole beside the
        world, the thing given it as its screenshot (_attach_shot). EVERY THING STILL WAITING, however many - each
        tried FIND_TRIES_MOST times at most. How many were found."""
        found = 0
        with self._lock:
            world = self.world()
            shown = {str(i.get(k)) for i in world["items"] for k in ("picture_of", "shot_of") if i.get(k)}
            zones = {z["id"]: z for z in world["zones"]}
            asked = []
            for item in world["items"]:
                if item.get("look_for") and not item.get("shot") and not item.get("picture") \
                        and int(item.get("find_tries") or 0) < self.FIND_TRIES_MOST:
                    zone = zones.get(item.get("zone")) or {}
                    asked.append({"item": item["id"], "look_for": item["look_for"], "zone": zone.get("id", ""),
                                  "task": str(zone.get("task") or zone.get("part_of") or (zone.get("tasks") or [""])[0]),
                                  "what": " - ".join(x for x in (item.get("name"), item.get("why")) if x)[:300],
                                  "card": {"title": item.get("card_title", ""), "statement": item.get("card_statement", ""),
                                           "status": item.get("card_status", "")} if item.get("card_title") else None})
        for one in asked:
            out = self.folder / "pictures" / f"found-{re.sub(r'[^A-Za-z0-9_-]', '', one['item'])}.png"
            theirs = [p for p in list((getattr(self, "_pictures_by_task", None) or {}).get(one["task"]) or [])
                      + list(getattr(self, "_pictures_anywhere", None) or []) if str(p.get("id")) not in shown][:8]
            sources = list((getattr(self, "_sources_by_task", None) or {}).get(one["task"]) or [])[:self.ROOM_SOURCES_MOST]
            sources += self._their_files(" ".join(x for x in ((one["card"] or {}).get("title", ""), one["look_for"]) if x))
            picture, what, ok = None, "", False
            try:
                choices = playfind.find(self.models, one["what"], one["look_for"], theirs, sources, one["card"])
            except Exception:
                log.info("a picture could not be found for a thing", exc_info=True)
                choices = []
            for choice in choices:
                picture, what, ok = None, "", False
                try:
                    mine = choice.get("theirs")
                    picture = next((p for p in theirs if p["id"] == mine), None) if mine else None
                    source = next((s for s in sources if s["id"] == mine), None) if mine else None
                    if picture is not None:
                        ok, what = True, picture.get("what", "")
                    elif source is not None and source["kind"] in ("file", "folder"):
                        ok, what = playfind.from_file(source["where"], out, self.config), source["title"]
                    elif source is not None:
                        ok, what = playfind.from_url(source["where"], out, self.config), source["title"]
                    elif choice.get("url"):
                        ok = playfind.from_url(choice["url"], out, self.config, allowed=choice["allowed"])
                        what = choice.get("what") or one["look_for"]
                except Exception:
                    log.info("a picture's choice could not be read", exc_info=True)
                    ok = False
                if ok:
                    with self._lock:                     # one alike shown already: the next choice
                        fresh = self.world()
                        path = picture.get("path") if picture is not None else out
                        alike = self._shown_alike(fresh, path)    # a paper's white first page is plain: not refused
                    if alike:
                        log.info("a found picture was alike one shown already - the next choice")
                        ok = False
                    elif not self._picture_is_it(path, one):  # LOOKED AT: not what was asked - the next choice
                        log.info("a found picture was not what was asked for - the next choice")
                        ok = False
                    else:
                        break
            if choices and not ok:
                log.info("no choice of %d could be read for a thing's picture", len(choices))
            with self._lock:
                fresh = self.world()
                item = next((i for i in fresh["items"] if i.get("id") == one["item"]
                             and i.get("look_for") == one["look_for"]), None)
                if item is None or item.get("shot") or item.get("picture"):
                    continue
                if not ok:                               # tried again in a later growth
                    item["find_tries"] = int(item.get("find_tries") or 0) + 1
                    self.save(fresh)
                    continue
                if picture is not None:                  # one of their screenshots
                    self._attach_shot(item, picture, str(picture["id"]))
                    shown.add(str(picture["id"]))
                else:
                    self._attach_shot(item, {"path": str(out), "what": what}, f"found-{one['item']}")
                if item.get("shot"):
                    self.save(fresh)
                    found += 1
        if asked:
            log.info("pictures found for the world's things: %d of %d asked", found, len(asked))
        return found

    def _picture_is_it(self, path: Any, one: Dict[str, Any]) -> bool:
        """THE PICTURE LOOKED AT BEFORE IT IS HUNG (playfind.FIND_LOOK_SYSTEM; the user, 2026-11-20): shown it, with what
        it was meant to show, the model says whether it is that. Taken as it is when it cannot be looked at."""
        photo = pieces.photo_block(path)
        if photo is None:
            return True
        asked = {"look_for": one.get("look_for", ""), "what": one.get("what", ""), **({"card": one["card"]} if one.get("card") else {})}
        try:
            said = self._call(playfind.FIND_LOOK_SYSTEM, playfind.TASK_FIND_LOOK,
                              [{"type": "text", "text": json.dumps(asked, ensure_ascii=False)},
                               {"type": "image", "source": {"type": "base64", **photo}}], 300, '"why"')
        except Exception:
            log.info("a found picture could not be looked at", exc_info=True)
            return True
        return said.get("fits") is not False

    #: How many of their files, and of the folders they are in, a find is offered (_their_files).
    THEIR_FILES_MOST, THEIR_FOLDERS_MOST = 6, 3

    def _their_files(self, words: str) -> List[Dict[str, Any]]:
        """THEIR FILES ON THEIR COMPUTER NOW, by a card's words (the local index - `files(words)`, each {"path"}): the
        files found and the folders they are in, each {"id": "file-N", "kind", "title", "where"} - shown to the model
        by name only. [] when there is no index or nothing found."""
        if not callable(self.files) or not str(words or "").strip():
            return []
        try:
            found = list(self.files(words) or [])
        except Exception:
            log.debug("their files could not be searched for a picture", exc_info=True)
            return []
        out: List[Dict[str, Any]] = []
        folders: List[str] = []
        for one in found:
            path = Path(str(one.get("path") or ""))
            if not path.name:
                continue
            if len([o for o in out if o["kind"] == "file"]) < self.THEIR_FILES_MOST:
                out.append({"id": f"file-{len(out) + 1}", "kind": "file", "title": path.name, "where": str(path)})
            folder = str(path.parent)
            if folder not in folders and len(folders) < self.THEIR_FOLDERS_MOST:
                folders.append(folder)
        for folder in folders:
            out.append({"id": f"file-{len(out) + 1}", "kind": "folder", "title": Path(folder).name + "/",
                        "where": folder})
        return out

    def _room_pictures(self, world: Dict[str, Any], zone: Dict[str, Any], task: str) -> Dict[str, Dict[str, Any]]:
        """THEIR PICTURES FOR THIS ROOM (its task's first, then the chat's), none shown in the world yet nor one alike:
        {id: picture} - kept for its laying (_lay_room)."""
        shown = {str(one.get(key)) for one in world["items"] for key in ("picture_of", "shot_of") if one.get(key)}
        out: Dict[str, Dict[str, Any]] = {}
        for one in list((getattr(self, "_pictures_by_task", None) or {}).get(task) or []) \
                + list(getattr(self, "_pictures_anywhere", None) or []):
            if len(out) >= self.ROOM_PICTURES_MOST:
                break
            ident = str(one.get("id") or "")
            if ident and ident not in shown and ident not in out and one.get("path") \
                    and self._picture_fits(world, zone, one.get("path")):
                out[ident] = one
        self.__dict__.setdefault("_room_offer", {})[zone["id"]] = out
        return out

    def _lay_room(self, world: Dict[str, Any], zone: Dict[str, Any], laid: Dict[str, List[Dict[str, Any]]], at: str,
                  idea: str, looked: str, adding: bool = False) -> bool:
        """THE ROOM AS DESIGNED, LAID: what was there let go (but what is kept, _kept_in_room) - ADDING, all of it
        stays; what lies under foot, stands on the floor, hangs on the wall (clear of what hangs there), is set on a
        table, hangs from above - then whatever cannot be walked to let go (_unreached). False when nothing was laid."""
        if not any(laid.get(layer) for layer in ("floor", "wall", "under", "on", "hang")):
            return False
        mine = [one for one in world["items"] if one["zone"] == zone["id"]]
        gone = set() if adding else {id(one) for one in mine if not self._kept_in_room(one)}
        world["items"] = [one for one in world["items"] if id(one) not in gone]
        wall_kept = set()
        for one in mine:
            if id(one) not in gone and (ITEM_KINDS.get(one["kind"]) or {}).get("wall"):
                wall_kept |= {x for x, _y in self._tiles_of(one)}
        offered = (getattr(self, "_room_offer", None) or {}).get(zone["id"]) or {}

        def add(one: Dict[str, Any], **more: Any) -> Optional[Dict[str, Any]]:
            spec = ITEM_KINDS.get(one["kind"]) or {}
            picture = offered.get(str(one.get("picture") or ""))
            if picture is not None and not self._picture_fits(world, zone, picture.get("path")):
                picture = None                           # that picture, or one alike, shown already
            name = widgets.widget_of(one["kind"])
            item = {"id": new_item_id(world), "zone": zone["id"], "kind": "widget" if name else one["kind"],
                    **({"widget": name} if name else {}), "x": one["x"], "y": one["y"],
                    "name": one.get("name", ""), "why": one.get("why", ""), "from": "", "made": at, "picture": "",
                    "designed": True, **more}
            self._mark_card(item, self._card_named(str(one.get("card") or "")))
            if one.get("news") and news:
                self._carry_news(item, news)
            if zone.get("great") and isinstance(one.get("stage"), int):
                item["stage"] = one["stage"]             # a great ground's stage, shown (_great_progress)
            if item.get("card") and item["card"] in featured:
                item["featured"] = featured[item["card"]]
            world["items"].append(item)
            if picture is not None:                      # ON THE MAP A CARTOON THING, THEIR PICTURE IN ITS CARD
                self._attach_shot(item, picture, str(one["picture"]))   # (_attach_shot - a frame or a poster too)
            if str(one.get("find_picture") or "").strip() and not item.get("shot") and not item.get("picture"):
                self._ask_to_find(item, zone, str(one["find_picture"]))
            for key in ("task_note", "fun_fact"):        # its card's own lines, where said
                if one.get(key):
                    item[key] = str(one[key])
            return item
        news = self._news_of(world, zone)
        featured = {f["card"]: f.get("why") or "" for f in ((world.get("great") or {}).get(str(zone.get("great")))
                                                            or {}).get("featured") or []} if zone.get("great") else {}
        hosts = {}
        for one in laid.get("under") or []:
            add(one)
        for one in laid.get("floor") or []:
            host = add(one)
            if host is not None:
                hosts[id(one)] = host
        for one in laid.get("wall") or []:
            w = item_size({"kind": one["kind"]})[0]
            if not set(range(one["x"], one["x"] + w)) & wall_kept:
                add({**one, "y": zone["y"] + 1})
        for one in laid.get("on") or []:
            host = hosts.get(id(one.get("on")))
            if host is not None:
                add({k: v for k, v in one.items() if k != "on"}, on=host["id"])
        for one in laid.get("hang") or []:
            add({k: v for k, v in one.items() if k != "hang"}, hang=True)
        zone["designed"] = at
        if idea and not (adding and zone.get("room_idea")):    # the room's idea its first design's
            zone["room_idea"] = idea[:300]
        if looked:
            zone["room_looked"] = looked[:300]
        floor = self._floor_tiles(zone)
        xs, ys = [x for x, _y in floor], [y for _x, y in floor]
        self._unreached(world, zone, floor, min(xs), min(ys), max(xs) + 1, max(ys) + 1)
        return True

    def _themes(self, since: str, until: str, lived: str, world: Dict[str, Any]) -> List[str]:
        """THE THEMES FIRST (the user: "先给topic和topic的description, 然后模型选topic再给这个topic的description"): the
        model given the themes in their groups, choosing a few; their things given to the growth after - with those of
        the lands the world already has. Nothing chosen (or the call failed): the lands' own only."""
        from ..common.languages import in_display
        from ..models.answers import read_text
        tree = catalogue.theme_tree()
        known = {theme for group in tree.values() for theme in group}
        lands = [str(region.get("biome") or "") for region in world.get("regions") or []]
        chosen: List[str] = []
        try:
            response = self.models.invoke(ModelRequest(
                system=THEMES_SYSTEM.format(most=THEMES_MOST), task=TASK_THEMES, max_tokens=200, builtin_web=False,
                messages=[{"role": "user", "content": json.dumps(
                    {"these_days": f"{since[:10]} to {until[:10]}", "what_they_lived_these_days": lived[:4000],
                     "lands_the_world_has": sorted(set(lands)), "THEMES": tree,
                     "themes_used_before": dict(sorted((world.get("themes_used") or {}).items(),
                                                       key=lambda one: -int(one[1] or 0)))}, ensure_ascii=False)}],
                language=in_language(self._play_code(), "nothing")), role="primary")
            text, _cut = read_text(response)
            match = re.search(r"\{.*\}", text or "", re.DOTALL)
            said = (json.loads(match.group(0)) if match else {}).get("themes") or []
            chosen = [str(one) for one in said if str(one) in known][:THEMES_MOST]
            if chosen:                                   # counted, to be told next time (themes_used_before)
                with self._lock:
                    fresh = self.world()
                    used = fresh.setdefault("themes_used", {})
                    for one in chosen:
                        used[one] = int(used.get(one) or 0) + 1
                    self.save(fresh)
        except Exception:
            log.info("the world's themes could not be chosen", exc_info=True)
        return list(dict.fromkeys([*chosen, *(land for land in lands if land in known)]))

    def _pick(self, said: Dict[str, Any]) -> None:
        """THINGS SEEN BEFORE CHOSEN (the user: "根据description可以打开多个图片比较"): each item that named some to
        compare is shown them side by side, all in one call; its "kind" set to the one chosen - the first named, when
        the call fails or says nothing of it."""
        from ..common.languages import in_display
        from ..models.answers import read_text
        choices = []
        for one in said.get("items") or []:
            if not isinstance(one, dict) or one.get("kind") in ITEM_KINDS:
                continue
            named = [str(x) for x in (one.get("compare") or []) if str(x) in catalogue.catalogue()["things"]]
            if named:
                choices.append((one, named[:catalogue.COMPARE_MOST]))
        if not choices:
            return
        for one, named in choices:
            one["kind"] = named[0]
        if len(choices) == 1 and len(choices[0][1]) == 1:
            return
        content: List[Dict[str, Any]] = []
        for n, (one, named) in enumerate(choices[:MOST_COMPARED], 1):
            picture = catalogue.compare_picture(named)
            if picture is None:
                continue
            content.append({"type": "text", "text": f"Choice {n}: {one.get('name') or ''} - {one.get('why') or ''}"[:400]
                                                    + f" (in {one.get('zone') or 'a place'}). Candidates: {', '.join(named)}."})
            content.append({"type": "image", "source": {"type": "base64", **picture}})
        if not content:
            return
        try:
            response = self.models.invoke(ModelRequest(
                system=PICK_SYSTEM, task=TASK_PICK, max_tokens=300, builtin_web=False,
                messages=[{"role": "user", "content": content}], language=in_language(self._play_code(), "nothing")), role="primary")
            text, _cut = read_text(response)
            match = re.search(r"\{.*\}", text or "", re.DOTALL)
            picks = (json.loads(match.group(0)) if match else {}).get("picks") or {}
        except Exception:
            log.info("the world's things could not be compared", exc_info=True)
            return
        for n, (one, named) in enumerate(choices[:MOST_COMPARED], 1):
            chosen = str(picks.get(str(n)) or "")
            if chosen in named:
                one["kind"] = chosen

    def _looked(self, since: str, until: str, lived: str, about: str) -> str:
        """FIRST, A SEARCH OF ITS OWN (the user: "允许它画之前先搜一轮"): the model says what it wants to know of these
        days; the agent's records are searched for it. "" when nothing was asked, or it could not be."""
        from ..common.languages import in_display
        from ..models.answers import read_text
        if not callable(self.look):
            return ""
        try:
            response = self.models.invoke(ModelRequest(
                system=LOOK_SYSTEM.format(most=LOOK_MOST), task=TASK_LOOK, max_tokens=400, builtin_web=False,
                messages=[{"role": "user", "content": json.dumps(
                    {"these_days": f"{since[:10]} to {until[:10]}", "what_they_lived_these_days": lived[:8000],
                     "who_they_are_now": about[:2000]}, ensure_ascii=False)}],
                language=in_language(self._play_code(), "the searches")), role="primary")
            text, _cut = read_text(response)
            match = re.search(r"\{.*\}", text or "", re.DOTALL)
            asked = (json.loads(match.group(0)) if match else {}).get("look_up") or []
        except Exception:
            log.info("the world's search could not be asked", exc_info=True)
            return ""
        asked = [str(one)[:120] for one in asked if isinstance(one, str) and one.strip()][:LOOK_MOST]
        if not asked:
            return ""
        try:
            return str(self.look(asked, until) or "")
        except Exception:
            log.info("the world's search could not be made", exc_info=True)
            return ""

    def apply(self, said: Dict[str, Any], offered: List[Dict[str, Any]], at: str,
              days: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
        """What the model added, checked against the kinds and laid out; pictures copied in; the days drawn kept as
        caught up. Kept."""
        with self._lock:
            world = self.world()
            closed = bool(world.get("regions") and world["regions"][0].get("closed") and world.get("zones"))
            if closed:
                # THE HOUSE ALONE (2026-11-19): no place, no land, no ground made yet - every thing in the house.
                home = world["zones"][0]["id"]
                said = {**said, "zones": [], "region": None, "story": [], "merge": [],
                        "items": [{**one, "zone": home} for one in said.get("items") or [] if isinstance(one, dict)],
                        "arrange": [{**one, "zone": home, "task": ""} for one in said.get("arrange") or [] if isinstance(one, dict)],
                        "decorate": [one for one in said.get("decorate") or [] if isinstance(one, dict) and one.get("zone") == home]}
                self._mood_opens = False
            by_name: Dict[str, Dict[str, Any]] = {}
            zones_added = items_added = 0
            switch = said.get("content_switch") if isinstance(said.get("content_switch"), dict) else {}
            switched = switch.get("switched") is True
            # A new region for a change, or a new zone, only when the content switched; leaving the village, or a
            # full region, needs no switch.
            opened = (self._open_region(world, said.get("region"), at, days)
                      if switched or getattr(self, "_mood_opens", False)
                      or regions.may_leave(world, self.drawn_days(world)) in ("leave", "full") else None)
            important = set(getattr(self, "_important", set()))
            self._important = set()                      # this growth's only
            apart_made = False
            for one in (said.get("zones") or [])[:MOST_NEW_ZONES + len(important)]:
                # New content, or an important task's own place (one it has not yet).
                for_task = str(one.get("task") or "") in important
                between = tuple(sorted(str(z) for z in (one.get("between") or []) if isinstance(z, str)))
                neighbours = getattr(self, "_between", {}).get(between)
                way = tuple(str(z) for z in (one.get("goes_on") or []) if isinstance(z, str))
                going = getattr(self, "_goes_on", {}).get(way)
                grouped = [str(t) for t in (one.get("tasks") or []) if str(t) in getattr(self, "_small_ids", set())]
                part_of = str(one.get("part_of") or "")
                plot = next((z for z in world["zones"] if z.get("plot") and z.get("task") == part_of), None)
                if not (switched or for_task or neighbours or going or grouped or plot):
                    continue
                kind = str(one.get("kind") or "")
                name = " ".join(str(one.get("name") or "").split())[:60]
                if kind not in ZONE_KINDS or kind in CODE_KINDS or not name:
                    continue
                info = (getattr(self, "_important_info", {}) or {}).get(str(one.get("task") or "")) or {}
                began = list(info["lived"]) if info.get("lived") and all(info["lived"]) else \
                    ([days[0][:10], days[1][:10]] if days else None)
                towards = neighbours["middle"] if neighbours else going["beyond"] if going else self._after(world, kind, began)
                like = next((z for z in world["zones"] if z["id"] == str(one.get("near") or "")), None)
                if like is not None and towards is not None and not neighbours and not going:
                    # Like with like: between where its time puts it and the place most like it - nearer the like.
                    lx, ly = places.centre(places.zone_rect(like))
                    towards = (lx * 0.6 + towards[0] * 0.4, ly * 0.6 + towards[1] * 0.4)
                apart_region = None
                if for_task and str(one.get("apart") or "") in regions.APART and not apart_made:
                    apart_region = regions.open_apart(world, str(one["apart"]), name, str(one.get("why") or ""), at,
                                                      CELL_W, CELL_H)
                    apart_made = True
                    towards = None
                if plot is not None and not for_task:
                    zone = self._add_in_plot(world, plot, kind, name, str(one.get("why") or ""), at)
                    if zone is not None:
                        zone["part_of"] = part_of
                else:
                    zone = self._add_zone(world, kind, name, str(one.get("why") or ""), at,
                                          region=apart_region, near=str(one.get("near") or ""), towards=towards)
                if zone is None:
                    continue
                if neighbours:                                # between its neighbours, of them
                    zone["between"] = list(neighbours["zones"])
                    zone["between_names"] = list(neighbours["projects"])
                    self._between.pop(between, None)
                if one.get("sign") in SIGN_STYLES:
                    zone["sign"] = str(one["sign"])
                road = muse.road_of(one.get("road"))
                if road:                                        # the way to it, as designed
                    zone["road"] = road
                card = self._card_named(str(one.get("card") or ""))
                if card is not None:                            # the place of a deliverable of the work
                    zone["card"], zone["card_title"] = card["id"], card["title"]
                if one.get("form") in FORMS and not ZONE_KINDS.get(kind, {}).get("outdoor"):
                    zone["form"] = str(one["form"])
                if one.get("shape") in shapes.OUTLINES:          # its own outline (agent/playshapes.py)
                    zone["shape"] = {"kind": str(one["shape"]), "seed": 0}
                caught = catch_of(one.get("catch")) if kind in WATER_KINDS else []
                if caught:
                    zone["catch"] = caught
                if grouped:                                   # small tasks, one whole scene
                    zone["tasks"] = grouped
                    zone["group_titles"] = [self._small_titles.get(t, t) for t in grouped]
                    self._small_ids -= set(grouped)
                    minutes = sum(self._small_minutes.get(t, 0) for t in grouped)
                    scale = task_scale(minutes)
                    if scale in ("full", "large", "estate"):
                        outdoor_g = bool(ZONE_KINDS.get(zone["kind"], {}).get("outdoor"))
                        span = places.SCALES.get(scale, (1, 1)) if scale != "full" else None
                        if span and not self._resize(world, zone, *places.most_size(span, CELL_W, CELL_H, outdoor_g), span):
                            self._resize(world, zone, *self._most(zone))
                        elif not span:
                            self._resize(world, zone, *self._most(zone))
                if going:                                     # on the way two came
                    zone["goes_on"] = list(going["zones"])
                    zone["goes_on_names"] = [going["from"]["name"], going["to"]["name"]]
                    if one.get("style") in places.STYLES and not ZONE_KINDS.get(kind, {}).get("outdoor"):
                        zone["style"] = str(one["style"])
                    self._goes_on.pop(way, None)
                if not for_task and not zone.get("part_of") and not grouped:
                    # A PLACE OF A TASK'S WORK KNOWS ITS TASK (the user, 2026-11-20: "为什么这几个没有被合并为一个大的区域" -
                    # two rooms of one project's work were made for new content with no task, and the project never had
                    # places enough to be a great ground): the task it named, else the one its name and why match.
                    named = str(one.get("about") or one.get("task") or "")
                    if named and named in (getattr(self, "_important_info", {}) or {}):
                        zone["part_of"] = named
                    else:
                        matched = self._task_by_words(world, f"{name} {one.get('why') or ''}")
                        if matched:
                            zone["part_of"] = matched
                if for_task:
                    zone["task"] = str(one["task"])
                    important.discard(zone["task"])
                    zone["task_title"] = str((getattr(self, "_important_info", {}).get(zone["task"]) or {}).get("title") or "")[:80]
                    # What the task was, as a whole - its sign tells it (api/web/play.js playSignCard).
                    zone["task_summary"] = str((getattr(self, "_important_info", {}).get(zone["task"]) or {}).get("summary") or "")[:300]
                    # As large as the task: its fullest in a cell, a wing, or an estate.
                    info = getattr(self, "_important_info", {}).get(zone["task"]) or {}
                    scale = info.get("scale", "first")
                    outdoor = bool(ZONE_KINDS.get(zone["kind"], {}).get("outdoor"))
                    if scale == "full":
                        self._resize(world, zone, *self._most(zone))
                    elif scale in ("large", "estate"):
                        span = places.SCALES[scale]
                        if not self._resize(world, zone, *places.most_size(span, CELL_W, CELL_H, outdoor), span):
                            self._resize(world, zone, *self._most(zone))
                    # ITS GROUNDS TOO, if the model gave it some: an open place beside it, the task's as well - as
                    # large as the task asks.
                    grounds = str(one.get("grounds") or "")
                    if grounds in places.OUTDOOR and not outdoor:
                        yard_name = " ".join(str(one.get("grounds_name") or "").split())[:60] or f"{name} {grounds}"
                        yard = self._add_zone(world, grounds, yard_name, str(one.get("why") or ""), at, near=zone["id"])
                        if yard is not None:
                            yard["task"], yard["task_title"], yard["grounds_of"] = zone["task"], zone["task_title"], zone["id"]
                            yard["task_summary"] = zone.get("task_summary", "")
                            if scale in ("large", "estate"):
                                if not self._resize(world, yard, *places.most_size((2, 1), CELL_W, CELL_H, True), (2, 1)):
                                    self._resize(world, yard, *self._most(yard))
                            elif scale == "full":
                                self._resize(world, yard, *self._most(yard))
                            by_name[yard_name.lower()] = yard
                            zones_added += 1
                music = tune_of(one.get("music"))
                if music:
                    zone["music"] = music
                info = (getattr(self, "_important_info", {}) or {}).get(str(zone.get("task") or "")) or {}
                if info.get("lived") and all(info["lived"]):
                    zone["days"] = list(info["lived"])
                elif days:
                    zone["days"] = [days[0][:10], days[1][:10]]
                by_name[name.lower()] = zone
                zones_added += 1
            by_id = {one["id"]: one for one in world["zones"]}
            for one in [x for x in (said.get("redesign") or []) if isinstance(x, dict)][:MOST_REDESIGNED]:
                if str(one.get("zone") or "") in by_id:
                    self._redesign(world, by_id[str(one["zone"])], one, at)
            for one in [x for x in (getattr(self, "_concepts", None) or said.get("concepts") or []) if isinstance(x, dict)][:12]:
                task = str(one.get("task") or "")
                zone = next((z for z in world["zones"] if z.get("task") == task and not z.get("grounds_of")), None)
                if zone is not None and str(one.get("idea") or "").strip():
                    zone["concept"] = {"idea": str(one["idea"])[:300],
                                       **({"block": " ".join(str(one["name"]).split())[:60]} if one.get("name") else {}),
                                       **({"project": str(one["project"])} if one.get("project") else {}),
                                       "associations": [str(a)[:40] for a in (one.get("associations") or [])][:8],
                                       "parts": [str(p)[:120] for p in (one.get("parts") or [])][:10],
                                       "fill": [str(k) for k in (one.get("fill") or []) if str(k) in ITEM_KINDS][:8],
                                       "at": at[:10]}
            for one in [x for x in (said.get("merge") or []) if isinstance(x, dict)][:MOST_MERGED]:
                self._merge(world, one, at)
            by_id = {one["id"]: one for one in world["zones"]}
            for one in [x for x in (said.get("enlarge") or []) if isinstance(x, dict)][:MOST_ENLARGED]:
                if str(one.get("zone") or "") in by_id:
                    self._enlarge(world, by_id[str(one["zone"])], str(one.get("why") or ""), at)
            pictures = {str(one["id"]): one for one in offered if one.get("id")}
            for one in (said.get("items") or [])[:MOST_NEW_ITEMS]:
                kind = str(one.get("kind") or "")
                if kind not in ITEM_KINDS:
                    continue
                where = str(one.get("zone") or "")
                zone = next((z for z in world["zones"] if z["id"] == where), None) or by_name.get(where.lower())
                if zone is None:
                    continue
                picture_id = str(one.get("picture") or "")
                if picture_id in pictures and not self._picture_fits(world, zone, pictures[picture_id].get("path")):
                    continue                           # that picture, or one alike, is shown already
                pixels = None
                if ITEM_KINDS[kind].get("pixels"):
                    pixels = drawn_pixels(one.get("pixels"), *ITEM_KINDS[kind]["pixels"])
                    if pixels is None:
                        continue                       # drawn by the agent, or not made
                item = self._add_item(world, zone, one, at)
                if item is None:
                    continue
                self._mark_card(item, self._card_named(str(one.get("card") or "")))
                if days:
                    item["days"] = [days[0][:10], days[1][:10]]       # the days it comes from
                if picture_id in pictures:                   # a cartoon thing on the map, their picture in its card
                    self._attach_shot(item, pictures[picture_id], picture_id)
                if pixels:
                    item["pixels"] = pixels
                if isinstance(one.get("find_picture"), str) and one["find_picture"].strip() and not item.get("picture"):
                    self._ask_to_find(item, zone, one["find_picture"])     # looked for (_find_pictures)
                items_added += 1
            self._decorate(world, said.get("decorate"), offered, by_name, at)
            if not closed:                              # a task's place and its ground: once there is a world outside
                self._fill_task_places(world, at, days)
                self._plots(world, at)
            for one in [x for x in (said.get("arrange") or []) if isinstance(x, dict)][:MOST_ARRANGED]:
                items_added += self._arrange(world, one, at)
            items_added += self._lay_story(world, said.get("story"), pictures, at)
            self._lay_regions(world)
            items_added += self._thicken(world, at)
            world["grown_at"] = at
            if days:
                world["caught_up"] = days[1]
            world["growths"].append({"at": at, "zones": zones_added, "items": items_added,
                                     "switched": switched, **({"what": str(switch.get("what") or "")[:200]} if switched else {}),
                                     **({"region": opened["id"]} if opened else {}),
                                     **({"days": [days[0][:10], days[1][:10]]} if days else {})})
            self.save(world)
            return {"zones": zones_added, "items": items_added, **({"region": opened["id"]} if opened else {})}

    def _full(self, world: Dict[str, Any], zone: Dict[str, Any]) -> bool:
        """No room left for a small thing, at its largest within its cells."""
        return (zone["w"], zone["h"]) >= self._most(zone) and self._place(world, zone, "plant") is None

    def _resize(self, world: Dict[str, Any], zone: Dict[str, Any], w: int, h: int,
                span: Optional[Tuple[int, int]] = None) -> bool:
        """A place made this size: where it stands if it fits there, else moved to the nearest room that holds it;
        its door anew. Whether it could be."""
        region = next((one for one in world["regions"] if one["id"] == zone.get("region")), world["regions"][0])
        others = [places.zone_rect(one) for one in world["zones"] if one["id"] != zone["id"]]
        here = places.find_spot(world, region, zone["kind"], w, h, (zone["x"], zone["y"]), zone["id"], others)
        b = places.bounds(region)
        r = places.footprint(zone["kind"], zone["x"], zone["y"], w, h)
        grown = [(o[0] - places.GAP, o[1] - places.GAP, o[2] + places.GAP, o[3] + places.GAP) for o in others]
        fits_land = r[0] >= b[0] and r[1] >= b[1] and r[2] <= b[2] and r[3] <= b[3] and not any(
            r[0] < g[2] and r[2] > g[0] and r[1] < g[3] and r[3] > g[1] for g in grown)
        # Where it stands, if it stays within its land's own outline (playplaces.within_shape) - or stood past it already
        # (laid before there was one: the world kept steady); else the nearest room that holds it; else where it stands
        # even past the outline - grown, rather than not at all.
        if fits_land and (places.within_shape(region, r) or not places.within_shape(region, places.zone_rect(zone))):
            spot = (zone["x"], zone["y"])
        elif here:
            spot = here
        elif fits_land:
            spot = (zone["x"], zone["y"])
        else:
            return False
        zone["x"], zone["y"], zone["w"], zone["h"] = spot[0], spot[1], w, h
        if span:
            zone["span"] = list(span)
        zone["door"] = zone["x"] + 1 + zone["w"] // 2
        return True

    def _move(self, world: Dict[str, Any], zone: Dict[str, Any], towards: Tuple[float, float],
              within: Optional[Tuple[int, int, int, int]] = None,
              keep_off: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """A place moved, as it is, to the room nearest `towards` (inside `within`, off `keep_off`); its things with it.
        Whether it could be."""
        region = next((one for one in world["regions"] if one["id"] == zone.get("region")), world["regions"][0])
        others = [places.zone_rect(one) for one in world["zones"] if one["id"] != zone["id"]
                  and not (within and one.get("plot"))]
        if keep_off:
            others.append(keep_off)
        spot = places.find_spot(world, region, zone["kind"], zone["w"], zone["h"], towards, zone["id"], others, within)
        if spot is None:
            return False
        dx, dy = spot[0] - zone["x"], spot[1] - zone["y"]
        zone["x"], zone["y"] = spot
        zone["door"] = zone["x"] + 1 + zone["w"] // 2
        for one in world["items"]:
            if one["zone"] == zone["id"]:
                one["x"] += dx
                one["y"] += dy
        return True

    def _merge(self, world: Dict[str, Any], said: Dict[str, Any], at: str) -> Optional[Dict[str, Any]]:
        """PLACES MERGED (the user: "任务之后可以合并，规划成一个小镇或者大house这种"): a TOWN - a square of its name made in
        their middle, each moved to stand round it, nothing else changed; or a GREAT HOUSE - one building as large as
        they need, each of them a room of it (its name, why, task and days kept in "rooms"), every thing carried in and
        laid out again; the places merged gone, nothing in them lost. The new place, or None."""
        import math
        by_id = {one["id"]: one for one in world["zones"]}
        members = [by_id[str(z)] for z in (said.get("zones") or []) if str(z) in by_id]
        if len(members) < 2:
            return None
        region_id = members[0].get("region")
        members = [one for one in members if one.get("region") == region_id]
        if len(members) < 2:
            return None
        region = next((one for one in world["regions"] if one["id"] == region_id), world["regions"][-1])
        name = " ".join(str(said.get("name") or "").split())[:60] or " & ".join(one["name"] for one in members)[:60]
        why = str(said.get("why") or "")[:400]
        centres = [places.centre(places.zone_rect(one)) for one in members]
        middle = (sum(c[0] for c in centres) / len(centres), sum(c[1] for c in centres) / len(centres))
        days = [d for d in (places.lived(world, one) for one in members) if d]
        lived = [min(d[0] for d in days), max(d[1] for d in days)] if days else None
        if str(said.get("into")) == "house":
            indoor = [one for one in members if not ZONE_KINDS.get(one["kind"], {}).get("outdoor")]
            if len(indoor) < 2:
                return None
            span = (2, 1) if len(indoor) == 2 else (2, 2)
            house = self._add_zone(world, "home", name, why, at, region=region, towards=middle)
            if house is None:
                return None
            if not self._resize(world, house, *places.most_size(span, CELL_W, CELL_H, False), span):
                self._resize(world, house, *self._most(house))
            style = str(said.get("style") or "mansion")
            house["style"] = style if style in places.STYLES else "mansion"
            house["rooms"] = [{key: one.get(key) for key in ("name", "why", "task", "task_title", "days", "made", "kind")
                               if one.get(key)} for one in indoor]
            house["tasks"] = [one["task"] for one in indoor if one.get("task")]
            if lived:
                house["days"] = lived
            gone = {one["id"] for one in indoor}
            names = {one["id"]: one["name"] for one in indoor}
            for one in world["items"]:
                if one["zone"] in gone:
                    one["room"] = names[one["zone"]]      # which room it came from
                    one["zone"] = house["id"]
            world["zones"] = [one for one in world["zones"] if one["id"] not in gone]
            for one in world["zones"]:                    # a yard of a merged place: the house's now
                if one.get("grounds_of") in gone:
                    one["grounds_of"] = house["id"]
            self._relayout(world, house)
            house["grown"].append({"at": at, "what": f"made of {len(indoor)} places", "why": why[:300]})
            return house
        # A TOWN: its square in their middle, they round it.
        square = self._add_zone(world, "plaza", name, why, at, region=region, towards=middle)
        if square is None:
            return None
        square["town"] = [one["id"] for one in members]
        square["town_names"] = [one["name"] for one in members]
        if lived:
            square["days"] = lived
        sx, sy = places.centre(places.zone_rect(square))
        for n, one in enumerate(members):
            a = n / len(members) * 2 * math.pi
            self._move(world, one, (sx + math.cos(a) * 12, sy + math.sin(a) * 9))
            one["in_town"] = square["id"]
        return square

    # -- a project's plot -----------------------------------------------------------------------------------------------
    def _project_zones(self, world: Dict[str, Any], task: str) -> List[Dict[str, Any]]:
        """A project's places: its task's own, their grounds, its parts, and any place carrying its look (the same name
        on the floor, the same task title) - "WaterMelon" twice is one project."""
        mine = [z for z in world["zones"] if not z.get("plot") and (z.get("task") == task or z.get("part_of") == task)]
        names = {str(z.get("floor_text") or "").lower() for z in mine if z.get("floor_text")} | \
            {str(z.get("task_title") or "").lower() for z in mine if z.get("task_title")}
        names.discard("")
        ids = {z["id"] for z in mine}
        for z in world["zones"]:
            if z["id"] in ids or z.get("plot"):
                continue
            if z.get("grounds_of") in ids or str(z.get("floor_text") or "").lower() in names or \
                    (z.get("task_title") and str(z["task_title"]).lower() in names):
                mine.append(z)
                ids.add(z["id"])
        return mine

    def _plots(self, world: Dict[str, Any], at: str) -> None:
        """EACH IMPORTANT TASK'S GROUND: a plot as large as its hours (plot_size) round its places - in its region, the
        places beside it that are not its moved away to make room; wider than DISTRICT_PART of a region, a region of
        its own, a district - its places gathered into it, their things with them. A PROJECT OF SEVERAL TASKS IS ONE
        GROUND (the user: "给他们的空间是在一起的，分成好几块但是在一起"): one district for all of it, as many region cells
        as its blocks need, a block each, side by side."""
        info = getattr(self, "_important_info", {}) or {}
        book = world.get("tasks") or {}
        todo: Dict[str, float] = {task: float(one.get("weight") or 0) for task, one in info.items() if not one.get("small")}
        for task in list(todo):                        # the rest of its project, drawn before, gathered with it
            project = self._project_of(world, task)
            for other in (project or {}).get("tasks") or []:
                if other not in todo and float((book.get(other) or {}).get("minutes") or 0) >= TASK_MINUTES:
                    todo[other] = float(book[other]["minutes"])
        greats = {str(z.get("great")) for z in world["zones"] if z.get("great")}
        for task, minutes in sorted(todo.items(), key=lambda one: -one[1]):
            if minutes < TASK_MINUTES:
                continue
            if str((self._project_of(world, task) or {}).get("id") or "") in greats:
                continue                                 # one great ground (_great_grounds): no blocks laid for it
            mine = self._project_zones(world, task)
            main = next((z for z in mine if z.get("task") == task and not z.get("grounds_of") and not z.get("part_of")), None)
            if main is None:
                continue
            plot = next((z for z in world["zones"] if z.get("plot") and z.get("task") == task), None)
            w, h = plot_size(minutes)
            project = self._project_of(world, task)
            fellows = [t for t in (project or {}).get("tasks") or []
                       if float((book.get(t) or {}).get("minutes") or todo.get(t) or 0) >= TASK_MINUTES]
            together = len(fellows) >= 2
            home = next((r for r in world["regions"] if r["id"] == main.get("region")), world["regions"][-1])
            if together:
                region = self._district(world, project["id"], main, at, name=project.get("name") or "",
                                        area=sum(a * b for a, b in (plot_size(float((book.get(t) or {}).get("minutes")
                                                                                     or todo.get(t) or 0)) for t in fellows)))
            else:
                b = places.bounds(home)
                region = home
                if w > (b[2] - b[0]) * DISTRICT_PART or h > (b[3] - b[1]) * DISTRICT_PART:
                    region = self._district(world, task, main, at)
            if region is not home or region.get("district_of"):
                if region.get("district_of") in (task, (project or {}).get("id")):
                    if not together or task == max(fellows, key=lambda t: float((book.get(t) or {}).get("minutes") or todo.get(t) or 0)):
                        main["landmark"] = True        # a district's main place: its landmark, seen from afar
                b = places.bounds(region)
                w, h = min(w, b[2] - b[0] - 6), min(h, b[3] - b[1] - 6)
            if plot is not None and plot.get("region") == region["id"] and plot["w"] >= w and plot["h"] >= h:
                room = (main["x"] - 1, main["y"] - LANDMARK_ROOM, main["x"] + main["w"] + 3, main["y"]) \
                    if main.get("landmark") else None
                self._fill_plot(world, plot, main, at, keep_off=room)
                continue
            if together:
                rect = self._block_rect(world, region, plot, w, h, fellows, mine)
            elif plot is not None and plot.get("region") == region["id"]:
                rect = self._plot_rect(world, region, plot, w, h, mine)
            else:
                rect = self._plot_rect(world, region, main if main.get("region") == region["id"] else None, w, h, mine)
            if rect is None:
                continue
            if plot is None:
                plot = {"id": new_zone_id(world), "kind": "estate", "name": str(main["name"])[:60],
                        "why": f"All of {main['name']}, as large as the time you gave it.", "made": at, "plot": True,
                        "task": task, "task_title": main.get("task_title", ""), "span": [1, 1], "style": "",
                        "grown": [], "door": 0}
                if main.get("days"):
                    plot["days"] = list(main["days"])
                world["zones"].insert(0, plot)           # laid first: its places drawn over it
            else:
                plot["grown"].append({"at": at, "what": f"{plot.get('w')} x {plot.get('h')} -> {rect[2]} x {rect[3]}",
                                      "why": "more time given to it"})
            # GROWN MUCH (the user: "如果对原task影响比较大，可以调整原task内部的layout"): its inside laid again - its
            # places gathered afresh, not only those outside it.
            again = plot.get("laid_area") and rect[2] * rect[3] >= RELAY_INSIDE * float(plot["laid_area"])
            plot.update({"region": region["id"], "x": rect[0], "y": rect[1] - 1, "w": rect[2], "h": rect[3]})
            if again or not plot.get("laid_area"):
                plot["laid_area"] = rect[2] * rect[3]
            inside = (rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3])
            # ITS PLACES GATHERED INTO IT, THE LARGEST FIRST (2026-11-18: the main one put in its middle cut the plot
            # into strips too narrow for the next - a 34 x 14 studio found no room): the main one at its top, room
            # kept above it for its landmark; the rest drawn to its corners and edges, its middle left open for the
            # scenes that fill it.
            room = None
            left, top, right, bottom = inside
            slots = [(left, bottom), (right, bottom), (left, (top + bottom) / 2), (right, (top + bottom) / 2),
                     ((left + right) / 2, bottom), (left, top), (right, top)]
            n = 0
            for z in sorted(mine, key=lambda one: (one is not main, -(one["w"] * one["h"]))):
                zr = places.zone_rect(z)
                there = not again and z.get("region") == region["id"] and zr[0] >= inside[0] and zr[1] >= inside[1] \
                    and zr[2] <= inside[2] and zr[3] <= inside[3]
                if not there:
                    was = z.get("region")
                    z["region"] = region["id"]
                    if z is main:
                        towards = ((left + right) / 2,
                                   top + (LANDMARK_ROOM if main.get("landmark") else 0) + (zr[3] - zr[1]) / 2)
                    else:
                        towards = slots[n % len(slots)]
                        n += 1
                    if not self._move(world, z, towards, within=inside, keep_off=room):
                        z["region"] = was
                if z is main and main.get("landmark"):        # nothing stands where its landmark rises
                    room = (main["x"] - 1, main["y"] - LANDMARK_ROOM, main["x"] + main["w"] + 3, main["y"])
            self._fill_plot(world, plot, main, at, keep_off=room)

    def _follow(self, world: Dict[str, Any], lineage: Dict[str, Dict[str, Any]]) -> None:
        """AS THE TASKS WENT (heartbeat/states: a task's born_of). A task JOINED INTO another, or one of those another
        was MERGED FROM or CARRIED from: its places are that one's now (their old task kept as "was_tasks"), its days
        added to it, its ground let go - the one it joined grows to hold them (_plots, built on where it is). A task
        SPLIT FROM another: kept as split from it, so its land is laid beside its parent's (_lay_regions)."""
        into: Dict[str, str] = {}
        alive = lambda one: not (lineage.get(one) or {}).get("retired")
        for task, born in lineage.items():
            born = born or {}
            if born.get("joined_into") and alive(str(born["joined_into"])):
                into[task] = str(born["joined_into"])
            if born.get("kind") in ("merged from", "carried") and alive(task):
                for one in born.get("from") or []:
                    into.setdefault(str(one), task)

        def now_of(task: str) -> str:
            seen = set()
            while task in into and task not in seen and into[task] != task:
                seen.add(task)
                task = into[task]
            return task
        book = world.setdefault("tasks", {})
        for z in world["zones"]:
            task = str(z.get("task") or "")
            if task and now_of(task) != task:
                z.setdefault("was_tasks", []).append(task)
                z["task"] = now_of(task)
        # One ground a task: a joined one's let go, with what was laid on it.
        grounds: Dict[str, Dict[str, Any]] = {}
        gone = set()
        for z in sorted((z for z in world["zones"] if z.get("plot")), key=lambda z: -(z["w"] * z["h"])):
            if z["task"] in grounds:
                gone.add(z["id"])
            else:
                grounds[z["task"]] = z
        if gone:
            world["zones"] = [z for z in world["zones"] if z["id"] not in gone]
            world["items"] = [i for i in world["items"] if i.get("zone") not in gone]
        for task in list(book):
            final = now_of(task)
            if final != task:
                mine, theirs = book.pop(task), book.setdefault(final, {})
                days = dict(theirs.get("day_minutes") or {})
                for d, m in (mine.get("day_minutes") or {}).items():
                    days[d] = round(days.get(d, 0.0) + float(m), 1)
                theirs["day_minutes"] = {d: days[d] for d in sorted(days)[-DAYS_KEPT:]}
                theirs["days"] = sorted(theirs["day_minutes"])
                theirs["minutes"] = float(theirs.get("minutes") or 0) + float(mine.get("minutes") or 0)
        for project in world.get("projects") or []:
            project["tasks"] = list(dict.fromkeys(now_of(t) for t in project.get("tasks") or []))
        for task, born in lineage.items():
            if (born or {}).get("kind") == "split from" and (born.get("from") or []):
                parent = now_of(str(born["from"][0]))
                if parent != task:
                    book.setdefault(task, {})["split_from"] = parent

    def _order_by_time(self, world: Dict[str, Any], zone: Dict[str, Any]) -> int:
        """ITS WORK'S THINGS ALONG ONE WAY, IN TIME (the user, 2026-11-20: "这些数字完全没有顺序，还是要整体按照顺序aligned，
        这样比较好布置动线"): the room laid, its cards' things - standing on the floor - put in the order their cards were
        first worked on along the way from its door: the spots they stand on taken by how far they are walked from the
        door, the earliest card the nearest. Things change places only with things of their own size, so nothing is laid
        over another. How many moved."""
        floor = self._floor_tiles(zone)
        if not floor:
            return 0
        cards: Dict[str, Tuple[str, int]] = {}
        for tree in (world.get("work_trees") or {}).values():
            for n, card in enumerate(tree or []):
                cards[str(card.get("id"))] = (str(card.get("first") or "9999"), n)
        mine = [i for i in world["items"] if i.get("zone") == zone["id"] and str(i.get("card") or "") in cards
                and not i.get("hang") and not i.get("on") and i.get("kind") != "piece"]
        if len(mine) < 2:
            return 0
        # How far each floor tile is walked from the door (or the floor's bottom middle).
        xs = sorted({x for x, _y in floor})
        bottom = max(y for _x, y in floor)
        door = (int(zone.get("door") or xs[len(xs) // 2]), bottom) if self._has_door(zone) else (xs[len(xs) // 2], bottom)
        start = min(floor, key=lambda p: abs(p[0] - door[0]) + abs(p[1] - door[1]))
        far = {start: 0}
        queue = [start]
        while queue:
            x, y = queue.pop(0)
            for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nxt in floor and nxt not in far:
                    far[nxt] = far[(x, y)] + 1
                    queue.append(nxt)
        moved = 0
        by_size: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
        for one in mine:
            by_size.setdefault(tuple(item_size(one)), []).append(one)
        for _size, ones in by_size.items():
            if len(ones) < 2:
                continue
            spots = sorted(((one["x"], one["y"]) for one in ones), key=lambda p: (far.get(p, 9999), p[1], p[0]))
            ordered = sorted(ones, key=lambda one: cards[str(one["card"])])
            for one, (x, y) in zip(ordered, spots):
                if (one["x"], one["y"]) != (x, y):
                    one["x"], one["y"] = x, y
                    moved += 1
        return moved

    #: How alike a place's words and a task's must be to tie them (TASK_TIE_LEAST of the place's words found in the task's).
    TASK_TIE_LEAST = 0.34

    def _task_by_words(self, world: Dict[str, Any], text: str) -> str:
        """The task a place's words are of: each task's title, summary and cards' titles against the place's name and
        why - words in English, pairs of characters in Chinese; the best, when enough of the place's are there. ""."""
        def marks(value: Any) -> set:
            value = str(value or "").lower()
            words = {one for one in re.findall(r"[a-z][a-z0-9]{2,}", value)
                     if one not in {"the", "and", "for", "with", "its", "their", "this", "that", "where", "what"}}
            for run in re.findall(r"[\u3400-\u9fff]+", value):
                words |= {run[k:k + 2] for k in range(len(run) - 1)}
            return words
        mine = marks(text)
        if not mine:
            return ""
        trees = world.get("work_trees") or {}
        best, best_score = "", 0.0
        for task, info in (getattr(self, "_important_info", {}) or {}).items():
            theirs = marks(" ".join([str(info.get("title") or ""), str(info.get("summary") or "")]
                                    + [str(c.get("title") or "") for c in trees.get(str(task)) or []]))
            score = len(mine & theirs) / len(mine)
            if score > best_score:
                best, best_score = str(task), score
        return best if best_score >= self.TASK_TIE_LEAST else ""

    def _project_of(self, world: Dict[str, Any], task: str) -> Optional[Dict[str, Any]]:
        return next((p for p in world.get("projects") or [] if task in (p.get("tasks") or [])), None)

    def _district(self, world: Dict[str, Any], owner: str, main: Dict[str, Any], at: str, name: str = "",
                  area: int = 0) -> Dict[str, Any]:
        """A project grown larger than a region can hold it - or of several tasks: a region of its own beside the
        others, its land the same as where it began, named for it; spread over as many region cells as its ground
        needs (SPREAD_MOST each way), so a great project is great on the map too."""
        region = next((r for r in world["regions"] if r.get("district_of") == owner), None)
        if region is None:
            home = next((r for r in world["regions"] if r["id"] == main.get("region")), world["regions"][-1])
            style = {key: home.get(key) for key in regions.SETTINGS if home.get(key)}
            # ITS THEME THE SAME AS WHERE IT BEGAN (the user, 2026-11-20: "新的土地沿用当前主题"): the land's scene, weather,
            # look, edges, animals, road, music and the feeling it was opened from - kept as they are there.
            style.update({key: copy.deepcopy(home[key]) for key in DISTRICT_THEME if home.get(key)})
            style["landmark"] = ""                      # its project is its landmark: no square kept in its middle
            region = regions.open_region(world, style, (name or str(main.get("task_title") or main["name"]))[:60],
                                         f"{name or main['name']} grew too large for where it stood.", at, None, CELL_W, CELL_H)
            region["district_of"] = owner
        if name:
            region["name"] = name[:60]
        if area:
            b = places.bounds({**region, "spread": [1, 1]})
            one = (b[2] - b[0]) * (b[3] - b[1])
            cells = max(1, math.ceil(area * 1.15 / one))                 # a little room between its blocks
            sx = min(SPREAD_MOST, max(1, math.ceil(math.sqrt(cells))))
            sy = min(SPREAD_MOST, max(1, math.ceil(cells / sx)))
            if [sx, sy] != list(regions.spread_of(region)):
                region["spread"] = [sx, sy]
        return region

    def _block_rect(self, world: Dict[str, Any], region: Dict[str, Any], plot: Optional[Dict[str, Any]], w: int, h: int,
                    fellows: List[str], mine: List[Dict[str, Any]]) -> Optional[Tuple[int, int, int, int]]:
        """A block of a project in its district: beside its project's other blocks, never over one; the first, the
        largest, from the district's upper left (in its middle it left strips too narrow for the next)."""
        b = places.bounds(region)
        theirs = [places.zone_rect(z) for z in world["zones"] if z.get("plot") and z is not plot and z.get("region") == region["id"]]
        own = [r for z, r in ((z, places.zone_rect(z)) for z in world["zones"] if z.get("plot") and z is not plot
                              and z.get("region") == region["id"]) if z.get("task") in fellows]
        tx, ty = (sum((r[0] + r[2]) / 2 for r in own) / len(own), sum((r[1] + r[3]) / 2 for r in own) / len(own)) if own \
            else (b[0] + 2 + w / 2, b[1] + 2 + h / 2)
        spots = [(x, y) for y in range(b[1] + 2, b[3] - h - 1, 2) for x in range(b[0] + 2, b[2] - w - 1, 2)]
        spots.sort(key=lambda p: ((p[0] + w / 2 - tx) ** 2 + (p[1] + h / 2 - ty) ** 2, p))
        for x, y in spots:
            if any(r[0] < x + w + 3 and r[2] > x - 3 and r[1] < y + h + 3 and r[3] > y - 3 for r in theirs):
                continue
            got = self._plot_rect(world, region, {"kind": "estate", "x": x, "y": y - 1, "w": w, "h": h}, w, h, mine)
            if got is not None:
                return got
        return None

    # -- THE SHAPE OF THE USER'S DAYS -----------------------------------------------------------------------------
    def _lay_regions(self, world: Dict[str, Any]) -> None:
        """THE WORLD LAID AS THE USER'S DAYS ARE - A WEIGHTED LAYOUT (the user: "layout整体应该反映你的daily activity
        structure"; "connection的weight是两个project overlap时间除以max(各自的work时间)"): each land as heavy as the time
        of its tasks; two tied by the time they were worked on the same days (each day the less of the two) over the
        larger of their whole times. In each area its middle - the heaviest when it was first laid - stays (a steady
        world: "世界稳定一点"); the lands laid before keep their cells; a new one is placed beside those it is tied to,
        where it is nearest them, ringing the middle - and joined by its road to the land beside it it is most tied to
        (a tree from the middle). A NEW PROJECT TIED TO NOTHING THERE STARTS AN AREA OF ITS OWN, its middle ("B在另一个
        地区形成中心"), reached by the gates; a new land tied more to another area goes there. Two lands worked on the
        same days enough get a line between them ("通勤公交车，火车"). Lands apart stay. Its places and things move
        with each land."""
        book = world.get("tasks") or {}
        if not book or len(world.get("regions") or []) < 2:
            return
        in_region: Dict[str, List[str]] = {}
        seen = set()
        for z in world["zones"]:
            task = str(z.get("task") or "")
            if task and task not in seen and not z.get("plot") and not z.get("grounds_of") and not z.get("part_of"):
                seen.add(task)
                in_region.setdefault(str(z.get("region")), []).append(task)
        # Each land's time by day (its tasks' together), and in all.
        by_day: Dict[str, Dict[str, float]] = {}
        for r in world["regions"]:
            days: Dict[str, float] = {}
            for task in in_region.get(r["id"], []):
                one = book.get(task) or {}
                minutes = one.get("day_minutes") or {}
                if not minutes and one.get("days"):        # kept before its minutes by day: its time spread evenly
                    each = float(one.get("minutes") or 0) / max(1, len(one["days"]))
                    minutes = {d: each for d in one["days"]}
                for d, m in minutes.items():
                    days[d] = days.get(d, 0.0) + float(m)
            by_day[r["id"]] = days
        weight = {r["id"]: sum(float((book.get(t) or {}).get("minutes") or 0) for t in in_region.get(r["id"], []))
                  for r in world["regions"]}
        tie_cache: Dict[Tuple[str, str], float] = {}

        def tie(a: str, b: str) -> float:
            key = (a, b) if a < b else (b, a)
            if key not in tie_cache:
                da, db = by_day.get(a, {}), by_day.get(b, {})
                both = sum(min(m, db[d]) for d, m in da.items() if d in db)
                tied = both / max(1.0, sum(da.values()), sum(db.values())) if both else 0.0
                # SPLIT OUT OF IT: beside its parent's land, whatever their days.
                parents = {(book.get(t) or {}).get("split_from") for t in in_region.get(a, [])} | \
                    {(book.get(t) or {}).get("split_from") for t in in_region.get(b, [])}
                if set(in_region.get(a, [])) & parents or set(in_region.get(b, [])) & parents:
                    tied = max(tied, SPLIT_TIE)
                tie_cache[key] = tied
            return tie_cache[key]

        def middle(cell: Tuple[int, int], region: Dict[str, Any]) -> Tuple[float, float]:
            sx, sy = regions.spread_of(region)
            return cell[0] + (sx - 1) / 2, cell[1] + (sy - 1) / 2

        # A NEW LAND'S AREA: the one it is most tied to; tied to none, a project of its own starts an area apart.
        middles = world.setdefault("middles", {})
        for region in world["regions"]:
            if region.get("laid") or region.get("apart") or region.get("starter") or region.get("travel"):
                continue
            ties: Dict[str, float] = {}
            for other in world["regions"]:
                if other is not region and other.get("laid") and not other.get("apart"):
                    ties[regions.area_of(other)] = ties.get(regions.area_of(other), 0.0) + tie(region["id"], other["id"])
            for full in [a for a in ties if a != regions.area_of(region) and
                         regions.area_cells(world, a) + len(regions.cells_of(region)) > regions.AREA_REGIONS]:
                ties.pop(full)                           # a full area takes no more: the map kept small
            best = max(ties, key=lambda a: ties[a]) if ties else None
            if best is not None and ties[best] > 0:
                if best != regions.area_of(region):
                    region["area"] = best
            elif ties and region.get("district_of") and weight[region["id"]] >= TASK_MINUTES:
                area = f"a{1 + max(int(regions.area_of(r)[1:] or 1) for r in world['regions'])}"
                region["area"], region["from"] = area, None
                middles[area] = region["id"]
                taken = {c for r in world["regions"] if r is not region for c in regions.cells_of(r)}
                cell = next(c for c in regions.region_cells(len(taken) * 9 + 60)
                            if not any((c[0] + dx, c[1] + dy) in taken for dx in range(-1, regions.spread_of(region)[0] + 1)
                                       for dy in range(-1, regions.spread_of(region)[1] + 1)))
                self._shift_region(world, region, cell)
        commutes = []
        for area in dict.fromkeys(regions.area_of(r) for r in world["regions"]):
            mine = [r for r in world["regions"] if regions.area_of(r) == area and not r.get("apart")]
            if not mine:
                continue
            ids = {r["id"] for r in mine}
            by_id = {r["id"]: r for r in mine}
            order = {r["id"]: n for n, r in enumerate(mine)}
            others = {c for r in world["regions"] if r["id"] not in ids for c in regions.cells_of(r)}
            near_others = {(x + dx, y + dy) for x, y in others for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            centre = by_id.get(str(middles.get(area) or "")) or max(mine, key=lambda r: (weight[r["id"]], -order[r["id"]]))
            middles[area] = centre["id"]
            if centre.get("from"):
                centre["from"] = None                    # the middle: the others' roads come to it
            placed: Dict[str, Tuple[int, int]] = {centre["id"]: tuple(centre["cell"])}
            taken = set(regions.cells_of(centre))
            for r in sorted(mine, key=lambda r: order[r["id"]]):
                if r is centre or not r.get("laid"):
                    continue
                cells = set(regions.cells_of(r))
                if cells & taken or cells & near_others:
                    continue                             # grown onto another's: placed again, below
                placed[r["id"]] = tuple(r["cell"])
                taken |= cells
            hub = middle(tuple(centre["cell"]), centre)
            strongest = max([tie(a["id"], b["id"]) for a in mine for b in mine if a["id"] < b["id"]] + [1e-6])
            rest = [r for r in mine if r["id"] not in placed]
            while rest:
                region = max(rest, key=lambda r: (sum(tie(r["id"], s) for s in placed), weight[r["id"]], -order[r["id"]]))
                rest.remove(region)
                sx, sy = regions.spread_of(region)
                spots = {(x + dx - i, y + dy - j) for x, y in taken for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                         for i in range(sx) for j in range(sy)}
                best = None
                for spot in sorted(spots):
                    cells = {(spot[0] + i, spot[1] + j) for j in range(sy) for i in range(sx)}
                    if cells & taken or cells & near_others:
                        continue
                    here = middle(spot, region)
                    # Drawn to what it is tied to, as strongly as the tie (the strongest counting 1), held round the
                    # middle, and to the side less crowded - so the lands ring the main one rather than trail off.
                    pull = sum(tie(region["id"], s) / max(1.0, math.dist(here, middle(at, by_id[s])))
                               for s, at in placed.items()) / strongest
                    crowd = sum(1 for s, at in placed.items() if math.dist(here, middle(at, by_id[s])) < 1.6)
                    score = (pull - 0.35 * math.dist(here, hub) - 0.15 * crowd, -math.dist(here, hub))
                    if best is None or score > best[0]:
                        best = (score, spot, cells)
                if best is None:
                    continue
                _, spot, cells = best
                beside = [s for s, at in placed.items() if any(max(abs(c[0] - d[0]), abs(c[1] - d[1])) <= 1 for c in cells
                                                               for d in regions.cells_of({**by_id[s], "cell": list(at)}))]
                region["from"] = max(beside or placed, key=lambda s: (tie(region["id"], s),
                                                                      -math.dist(middle(spot, region), middle(placed[s], by_id[s]))))
                placed[region["id"]] = spot
                taken |= cells
            for rid, cell in placed.items():
                self._shift_region(world, by_id[rid], cell)
                by_id[rid]["laid"] = True
            # A COMMUTE: two lands tied enough, on enough days the same.
            pairs = sorted(((tie(a, b), a, b) for a in ids for b in ids if a < b), reverse=True)
            commutes += [{"from": a, "to": b, "tie": round(w, 3),
                          "days": len(set(by_day[a]) & set(by_day[b]))} for w, a, b in pairs
                         if w >= COMMUTE_TIE and len(set(by_day[a]) & set(by_day[b])) >= COMMUTE_DAYS][:COMMUTES_MOST]
        world["commutes"] = commutes[:COMMUTES_MOST * 2]

    def _shift_region(self, world: Dict[str, Any], region: Dict[str, Any], cell: Tuple[int, int]) -> None:
        """A region to another cell of the grid: its places and their things with it, as they stand."""
        ox, oy = regions.origin(cell, CELL_W, CELL_H)
        dx, dy = ox - region["ox"], oy - region["oy"]
        region.update({"cell": list(cell), "ox": ox, "oy": oy})
        if not dx and not dy:
            return
        ids = set()
        for z in world["zones"]:
            if z.get("region") != region["id"]:
                continue
            z["x"] += dx
            z["y"] += dy
            if not z.get("plot") and isinstance(z.get("door"), (int, float)):
                z["door"] += dx
            if z.get("keep_off"):
                k = z["keep_off"]
                z["keep_off"] = [k[0] + dx, k[1] + dy, k[2] + dx, k[3] + dy]
            ids.add(z["id"])
        for one in world["items"]:
            if one.get("zone") in ids:
                one["x"] += dx
                one["y"] += dy

    def _plot_rect(self, world: Dict[str, Any], region: Dict[str, Any], around: Optional[Dict[str, Any]], w: int, h: int,
                   mine: List[Dict[str, Any]]) -> Optional[Tuple[int, int, int, int]]:
        """Where a plot of w x h goes: round `around` (its main place) if given, else the region's heart; within the
        region; the places in the way that are not its own moved off it (the user: "把原来在旁边的没那么相关的其它区域
        移走"). (x, top, w, h), or None when it cannot be."""
        b = places.bounds(region)
        cx, cy = places.centre(places.zone_rect(around)) if around else places.heart(world, region)
        x = int(min(max(b[0], cx - w / 2), b[2] - w))
        y = int(min(max(b[1], cy - h / 2), b[3] - h))
        if x < b[0] or y < b[1]:
            return None
        rect = (x, y, x + w, y + h)
        ids = {z["id"] for z in mine}
        for z in list(world["zones"]):
            if z["id"] in ids or z.get("plot") and z.get("task") in {m.get("task") for m in mine}:
                continue
            if z.get("region") != region["id"]:
                continue
            zr = places.zone_rect(z)
            if zr[0] < rect[2] + places.GAP and zr[2] > rect[0] - places.GAP and zr[1] < rect[3] + places.GAP and zr[3] > rect[1] - places.GAP:
                if z.get("plot"):                         # another project's ground: this one stops short of it
                    return None
                zx, zy = places.centre(zr)
                away = (zx + (zx - (x + w / 2)) * 1.5, zy + (zy - (y + h / 2)) * 1.5)
                if self._move(world, z, away, keep_off=rect):
                    z.setdefault("grown", []).append({"at": "", "what": "moved aside", "why": "to make room for a project beside it"})
        return x, y, w, h

    def _add_in_plot(self, world: Dict[str, Any], plot: Dict[str, Any], kind: str, name: str, why: str,
                     at: str) -> Optional[Dict[str, Any]]:
        """A part of a project, stood inside its plot."""
        region = next((r for r in world["regions"] if r["id"] == plot.get("region")), world["regions"][-1])
        inside = (plot["x"], plot["y"] + 1, plot["x"] + plot["w"], plot["y"] + 1 + plot["h"])
        outdoor = bool(ZONE_KINDS.get(kind, {}).get("outdoor"))
        w, h = places.FIRST_OUTDOOR if outdoor else places.FIRST_INDOOR
        ident = new_zone_id(world)
        others = [places.zone_rect(one) for one in world["zones"] if not one.get("plot")]
        spot = places.find_spot(world, region, kind, w, h, places.centre(inside), ident, others, inside)
        if spot is None:
            return None
        zone = {"id": ident, "kind": kind, "name": name[:60], "why": why[:400], "made": at, "region": region["id"],
                "span": [1, 1], "style": FIRST_STYLE.get(kind, ""), "x": spot[0], "y": spot[1], "w": w, "h": h,
                "grown": [], "door": spot[0] + 1 + w // 2}
        world["zones"].append(zone)
        return zone

    def _fill_plot(self, world: Dict[str, Any], plot: Dict[str, Any], main: Dict[str, Any], at: str,
                   keep_off: Optional[Tuple[int, int, int, int]] = None) -> int:
        """A plot's open ground is the model's to arrange (_arrange) - nothing scattered by the code (the user: "模型不要随即
        摆"); only where its landmark rises is kept, for what is arranged later."""
        plot["keep_off"] = list(keep_off) if keep_off else []
        return 0

    def _blocked_in(self, world: Dict[str, Any], plot: Optional[Dict[str, Any]] = None) -> set:
        """Every tile taken: each place (a step round it) but the plot itself, each thing (a step round it), and the
        room kept for a landmark."""
        blocked = set()
        for z in world["zones"]:
            if plot is not None and z is plot:
                continue
            zr = places.zone_rect(z)
            blocked |= {(x, y) for x in range(zr[0] - 1, zr[2] + 1) for y in range(zr[1] - 1, zr[3] + 1)}
        for one in world["items"]:
            sw, sh = item_size(one)
            blocked |= {(one["x"] + dx, one["y"] + dy) for dx in range(-1, sw + 1) for dy in range(-1, sh + 1)}
        room = (plot or {}).get("keep_off") or []
        if room:
            blocked |= {(x, y) for x in range(room[0], room[2]) for y in range(room[1], room[3])}
        return blocked

    def _open_ground(self, world: Dict[str, Any], task: str) -> int:
        plot = next((z for z in world["zones"] if z.get("plot") and z.get("task") == task), None)
        if plot is None:
            return 0
        blocked = self._blocked_in(world, plot)
        left, top = plot["x"], plot["y"] + 1
        return sum(1 for y in range(top + 1, top + plot["h"] - 1) for x in range(left + 1, left + plot["w"] - 1)
                   if (x, y) not in blocked)

    def _arrange(self, world: Dict[str, Any], said: Dict[str, Any], at: str) -> int:
        """MANY OF ONE THING, ONE DESIGN (the user: "一个物品可以是多个，比如摆多张床，本质对应的是一个理由"): "count" of
        "kind" laid by "pattern" - in rows, a grid, a line, a ring, along the border, a cluster - in the part of the
        plot or the room "where" says, each on free ground, all of one group with one reason. How many were laid."""
        import math
        if str(said.get("pattern") or "") == "picture":
            return self._lay_picture(world, said, at)
        kind = str(said.get("kind") or "")
        if kind not in ITEM_KINDS or ITEM_KINDS[kind].get("wall") or alive(kind):
            return 0
        by_id = {z["id"]: z for z in world["zones"]}
        zone = by_id.get(str(said.get("zone") or "")) or next(
            (z for z in world["zones"] if z.get("plot") and z.get("task") == str(said.get("task") or "")), None)
        if zone is None:
            return 0
        if zone.get("plot"):
            left, top, w, h = zone["x"] + 1, zone["y"] + 2, zone["w"] - 2, zone["h"] - 2
        else:
            left, top, w, h = self.floor_of(zone)
        where = str(said.get("where") or "middle")
        if where.startswith("around:") and where[7:] in by_id:
            r = places.zone_rect(by_id[where[7:]])
            area = (max(left, r[0] - 4), max(top, r[1] - 4), min(left + w, r[2] + 4), min(top + h, r[3] + 4))
        else:
            third_w, third_h = max(3, w // 3), max(3, h // 3)
            area = {"north": (left, top, left + w, top + third_h), "south": (left, top + h - third_h, left + w, top + h),
                    "west": (left, top, left + third_w, top + h), "east": (left + w - third_w, top, left + w, top + h)}.get(
                where, (left + w // 4, top + h // 4, left + w - w // 4, top + h - h // 4))
        x0, y0, x1, y1 = area
        try:
            scale = max(1, min(SCALE_MOST, int(said.get("scale") or 1)))
        except (TypeError, ValueError):
            scale = 1
        if indoors(zone):                                # a room's things at their own size, always
            scale = 1
        sw, sh = item_size({"kind": kind, "scale": scale, "picture": said.get("picture")})
        principle = str(said.get("principle") or "")
        count = max(1, min(ARRANGED_MOST, int(said.get("count") or 1)))
        pattern = str(said.get("pattern") or "rows")
        pattern = pattern if pattern in PATTERNS else "rows"
        step_x, step_y = sw + 1, sh + (1 if pattern in ("grid", "cluster") else 2)
        spots: List[Tuple[int, int]] = []
        if pattern in ("rows", "grid"):
            spots = [(x, y) for y in range(y0, y1 - sh + 1, step_y) for x in range(x0, x1 - sw + 1, step_x)]
        elif pattern == "line":
            mid = (y0 + y1) // 2
            spots = [(x, mid) for x in range(x0, x1 - sw + 1, step_x)]
        elif pattern in ("ring", "border"):
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            rx, ry = max(2, (x1 - x0) / 2 - 1), max(2, (y1 - y0) / 2 - 1)
            if pattern == "border":
                spots = [(x, y0) for x in range(x0, x1 - sw + 1, step_x)] + [(x1 - sw, y) for y in range(y0, y1 - sh + 1, step_y)] + \
                        [(x, y1 - sh) for x in range(x1 - sw, x0 - 1, -step_x)] + [(x0, y) for y in range(y1 - sh, y0 - 1, -step_y)]
            else:
                around = max(4, int(2 * math.pi * min(rx, ry) / max(sw, sh) / 1.5))
                spots = [(int(round(cx + rx * math.cos(2 * math.pi * k / around))), int(round(cy + ry * math.sin(2 * math.pi * k / around))))
                         for k in range(around)]
        else:                                          # a cluster: from its middle outwards
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            spots = sorted(((x, y) for y in range(y0, y1 - sh + 1, step_y) for x in range(x0, x1 - sw + 1, step_x)),
                           key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        blocked = self._blocked_in(world, zone if zone.get("plot") else None)
        if not zone.get("plot"):                       # in a room: its walls, its door's way, its things
            blocked = {(x, y) for one in world["items"] if one["zone"] == zone["id"]
                       for (x, y) in self._tiles_of(one)} | places.inner_walls(zone)
        group = f"g{sum(1 for one in world['items'] if one.get('group')) + len(world['items'])}"
        name = " ".join(str(said.get("name") or "").split())[:60]
        why = str(said.get("why") or "")[:500]
        laid = 0
        for x, y in spots:
            if laid >= count:
                break
            tiles = {(x + dx, y + dy) for dx in range(sw) for dy in range(sh)}
            if tiles & blocked or x < left or y < top or x + sw > left + w or y + sh > top + h:
                continue
            world["items"].append({"id": new_item_id(world), "zone": zone["id"], "kind": kind, "x": x, "y": y,
                                   "name": name, "why": why, "from": "", "made": at, "picture": "", "group": group,
                                   **({"scale": scale} if scale > 1 else {}),
                                   **({"principle": principle} if principle in PRINCIPLES else {})})
            blocked |={(x + dx, y + dy) for dx in range(-1, sw + 1) for dy in range(-1, sh + 1)} if pattern != "rows" else tiles
            laid += 1
        return laid

    def _lay_picture(self, world: Dict[str, Any], said: Dict[str, Any], at: str) -> int:
        """A PICTURE LAID IN THINGS: its rows of letters, each a material - a kind of thing (one tile each) or paint on
        the ground ("#rrggbb") - laid from the top left of where "where" puts it (in the plot's middle by default),
        whole or not at all where taken; one group, one reason. How many tiles were laid."""
        picture = said.get("picture") if isinstance(said.get("picture"), dict) else {}
        rows = [str(r)[:PICTURE_SIDE] for r in (picture.get("rows") or [])][:PICTURE_SIDE]
        legend = {}
        for letter, what in (picture.get("legend") or {}).items():
            what = str(what or "")
            if len(str(letter)) != 1 or str(letter) == ".":
                continue
            if re.fullmatch(r"#[0-9a-fA-F]{6}", what):
                legend[str(letter)] = ("paint", what.lower())
            elif what in ITEM_KINDS and not ITEM_KINDS[what].get("wall") and ITEM_KINDS[what]["size"] == (1, 1)                     and not alive(what):
                legend[str(letter)] = (what, "")
        if not rows or not legend:
            return 0
        by_id = {z["id"]: z for z in world["zones"]}
        zone = by_id.get(str(said.get("zone") or "")) or next(
            (z for z in world["zones"] if z.get("plot") and z.get("task") == str(said.get("task") or "")), None)
        if zone is None:
            return 0
        if zone.get("plot"):
            left, top, w, h = zone["x"] + 1, zone["y"] + 2, zone["w"] - 2, zone["h"] - 2
        else:
            left, top, w, h = self.floor_of(zone)
        pw_, ph_ = max(len(r) for r in rows), len(rows)
        if pw_ > w or ph_ > h:
            return 0                                   # too large for its ground: not laid cut short
        where = str(said.get("where") or "middle")
        x0 = {"west": left, "east": left + w - pw_}.get(where, left + (w - pw_) // 2)
        y0 = {"north": top, "south": top + h - ph_}.get(where, top + (h - ph_) // 2)
        if where.startswith("around:") and where[7:] in by_id:
            r = places.zone_rect(by_id[where[7:]])
            x0 = int(min(max(left, (r[0] + r[2]) / 2 - pw_ / 2), left + w - pw_))
            y0 = int(min(max(top, r[3] + 1), top + h - ph_))
        blocked = self._blocked_in(world, zone if zone.get("plot") else None)
        tiles = [(x0 + cx, y0 + cy, legend[ch]) for cy, row in enumerate(rows) for cx, ch in enumerate(row) if ch in legend]
        tiles = tiles[:PICTURE_MOST]
        # Its things kept off what stands there; its paint goes under all but buildings.
        clear = [(x, y, one) for x, y, one in tiles if (x, y) not in blocked or one[0] == "paint"]
        if len(clear) < len(tiles) * 0.9:
            return 0                                   # most of it would be broken: not laid at all
        group = f"g{sum(1 for one in world['items'] if one.get('group')) + len(world['items'])}"
        name = " ".join(str(said.get("name") or "").split())[:60]
        why = str(said.get("why") or "")[:500]
        for x, y, (kind, colour) in clear:
            world["items"].append({"id": new_item_id(world), "zone": zone["id"], "kind": kind, "x": x, "y": y,
                                   "name": name, "why": why, "from": "", "made": at, "picture": "", "group": group,
                                   **({"colour": colour} if colour else {})})
        return len(clear)

    @staticmethod
    def _tiles_of(one: Dict[str, Any]) -> set:
        sw, sh = item_size(one)
        return {(one["x"] + dx, one["y"] + dy) for dx in range(sw) for dy in range(sh)}

    def _floor_tiles(self, zone: Dict[str, Any]) -> set:
        """A place's floor, tile by tile: a plot's open ground inside its edge, a room's inside its walls."""
        if zone.get("great") and not indoors(zone):      # an open part: its outline (playgreat), as the page lays it
            return great.shape_tiles(zone.get("shape"), (zone["x"], zone["y"] + 1, zone["w"], zone["h"]))
        # (a hall of a great ground under a roof: as any room - of its own outline, its walls along it; a side agent,
        # 2026-11-19: its walls had been laid square round a floor of its outline)
        if zone.get("plot"):
            left, top, w, h = zone["x"] + 1, zone["y"] + 2, zone["w"] - 2, zone["h"] - 2
        else:
            left, top, w, h = self.floor_of(zone)
        if indoors(zone) and (zone.get("shape") or {}).get("kind", "rect") != "rect":   # its own outline: no walls across
            return self.shaped_room(zone, left, top, w, h)
        tiles = {(x, y) for x in range(left, left + w) for y in range(top, top + h)} - places.inner_walls(zone)
        if zone.get("shape") and not indoors(zone):      # an open place of its own outline
            tiles = {one for one in tiles if great.shape_has(zone["shape"], (left, top, w, h), *one)}
        return tiles

    @classmethod
    def shaped_room(cls, zone: Dict[str, Any], left: int, top: int, w: int, h: int) -> set:
        """A ROOM UNDER A ROOF OF ITS OWN OUTLINE (a star, a triangle, a circle - api/web/play-regions.js
        playShapedRoom, the same tiles): its outline within its floor, a way in from its door straight up to it;
        its walls the outline's own edge (a tile of it beside one outside, within the box), the floor what is left."""
        inside = {(x, y) for x in range(left, left + w) for y in range(top, top + h)
                  if great.shape_has(zone["shape"], (left, top, w, h), x, y)}
        door = cls.door_of(zone)
        way = set()                                     # the way in, up from the door into the outline
        for x in range(door, door + DOOR_W):
            for y in range(top + h - 1, top - 1, -1):
                way.add((x, y))
                if (x, y) in inside:
                    break
        inside |= way
        rim = {(x, y) for x, y in inside
               if any((x + dx, y + dy) not in inside and left <= x + dx < left + w and top <= y + dy < top + h
                      for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))}
        # HOW OPEN, AS ANY HOUSE (a side agent, 2026-11-19; the user: "这些也可以开放"): a pavilion no wall; open in front
        # or round a court, its back half walled and its front half open; closed, walled all round.
        form = zone.get("form") or "closed"
        if form == "pavilion":
            return inside
        if form in ("open_front", "u_court"):
            rim = {(x, y) for x, y in rim if 2 * (y - top) < h}
        return inside - (rim - way)

    def _free_share(self, world: Dict[str, Any], zone: Dict[str, Any]) -> float:
        """How much of a place's floor nothing stands on (or lies on)."""
        floor = self._floor_tiles(zone)
        if not floor:
            return 0.0
        covered = set()
        for one in world["items"]:
            if one["zone"] == zone["id"] and not (ITEM_KINDS.get(one["kind"]) or {}).get("wall") \
                    and not one.get("on") and not one.get("hang"):
                covered |= self._tiles_of(one)
        return 1 - len(covered & floor) / len(floor)

    # -- pictures: none twice, none alike (picture_print) -------------------------------------------------------------
    def _print_of(self, one: Dict[str, Any]) -> Optional[Tuple[int, float]]:
        """A framed thing's picture print, kept on it once made."""
        if one.get("print"):
            try:
                return int(one["print"], 16), float(one.get("print_colour") or 0)
            except (TypeError, ValueError):
                pass
        folder = getattr(self, "folder", None)
        if not one.get("picture") or folder is None:
            return None
        made = picture_print(Path(folder) / "pictures" / str(one["picture"]))
        if made:
            one["print"], one["print_colour"] = f"{made[0]:016x}", round(made[1], 1)
        return made

    def _picture_fits(self, world: Dict[str, Any], zone: Dict[str, Any], source: Any) -> bool:
        """Whether a picture may be framed in `zone`: none alike shown anywhere, and a plain one only where none is."""
        made = picture_print(source)
        if made is None:
            return True
        for one in world["items"]:
            shown = self._print_of(one)
            if shown is None:
                continue
            if prints_alike(shown[0], made[0]):
                return False
            if made[1] < PLAIN_COLOUR and shown[1] < PLAIN_COLOUR and one["zone"] == zone["id"]:
                return False
        return True

    def _shown_alike(self, world: Dict[str, Any], source: Any) -> bool:
        """Whether a picture alike this one is shown on a thing already (its print)."""
        made = picture_print(source)
        if made is None:
            return False
        return any(shown is not None and prints_alike(shown[0], made[0])
                   for shown in (self._print_of(one) for one in world["items"]))

    def _keep_print(self, item: Dict[str, Any], source: Any) -> None:
        made = picture_print(source)
        if made:
            item["print"], item["print_colour"] = f"{made[0]:016x}", round(made[1], 1)

    def _drop_repeated_pictures(self, world: Dict[str, Any]) -> int:
        """THE SAME PICTURE NOT KEPT AGAIN AND AGAIN: a picture the code framed from a task's materials (its "from" a
        picture's) that is alike one shown before it - each growth had framed the same ones anew - let go. What the
        model chose itself stays."""
        kept: List[int] = []
        out, dropped = [], 0
        for one in sorted(world["items"], key=lambda item: item.get("made", "")):
            made = self._print_of(one)
            if made is not None:
                if str(one.get("from") or "").startswith("picture: ") and any(prints_alike(made[0], k) for k in kept):
                    dropped += 1
                    continue
                kept.append(made[0])
            out.append(one)
        if dropped:
            order = {id(one): n for n, one in enumerate(world["items"])}
            world["items"] = sorted(out, key=lambda one: order[id(one)])
        return dropped

    def _lay_story(self, world: Dict[str, Any], said: Any, pictures: Dict[str, Dict[str, Any]], at: str) -> int:
        """A PROJECT'S STORY SET UP IN ITS PLACE (project_story; the user, 2026-11-18: "你要让user 看到他的成果是如何一步步
        变好的"): each step the model told - its name, its words, its picture - a station of the project's own ground (its
        plot, else its main place), in the order of the story: across, then down; its picture large on a stand, or,
        with none, a signpost; a way kept round each. A step already set up is never set up again. How many were."""
        if not isinstance(said, list):
            return 0
        told = {(str(one.get("story_of")), str(one.get("story_step"))) for one in world["items"] if one.get("story_of")}
        by_task: Dict[str, List[Dict[str, Any]]] = {}
        for one in said[:40]:
            if not isinstance(one, dict):
                continue
            task, step = str(one.get("task") or ""), str(one.get("step") or "")
            if task and re.fullmatch(r"\d{4}-\d{2}-[012]", step) and (task, step) not in told:
                by_task.setdefault(task, []).append(one)
        laid = 0
        for task, steps in by_task.items():
            homes = [z for z in world["zones"] if z.get("task") == task]
            home = next((z for z in homes if z.get("plot")), None) or next(
                (z for z in homes if not z.get("grounds_of")), None) or (homes[0] if homes else None)
            if home is None:
                continue
            floor = self._floor_tiles(home)
            taken = set(places.inner_walls(home))
            for one in world["items"]:
                if one["zone"] == home["id"]:
                    sw, sh = item_size(one)
                    taken |= {(one["x"] + dx, one["y"] + dy) for dx in range(-1, sw + 1) for dy in range(-1, sh + 1)}
            if home.get("plot"):
                taken |= self._blocked_in(world, home)
            if self._has_door(home):
                taken |= {(home["door"] + d, y) for d in range(DOOR_W) for (_x, y) in floor}
            spots = sorted(floor, key=lambda p: (p[1], p[0]))
            for one in sorted(steps, key=lambda s: str(s.get("step"))):
                picture = pictures.get(str(one.get("picture") or ""))
                kind = "statue_of" if picture else "signpost"
                if kind not in ITEM_KINDS:
                    kind = "signpost" if "signpost" in ITEM_KINDS else "statue_of"
                scale = 2 if picture and not indoors(home) else 1
                sw, sh = item_size({"kind": kind, "scale": scale, "picture": picture and "x"})
                spot = next(((x, y) for x, y in spots
                             if {(x + dx, y + dy) for dx in range(sw) for dy in range(sh)} <= floor
                             and not {(x + dx, y + dy) for dx in range(-1, sw + 1) for dy in range(-1, sh + 1)} & taken),
                            None)
                if spot is None:
                    break                                      # no room left: the rest at a later growth
                item = {"id": new_item_id(world), "zone": home["id"], "kind": kind, "x": spot[0], "y": spot[1],
                        "name": " ".join(str(one.get("name") or "").split())[:60],
                        "why": str(one.get("why") or "")[:500], "from": f"the story of the work, {one['step']}",
                        "made": at, "picture": "", "story_of": task, "story_step": str(one["step"]),
                        **({"scale": scale} if scale > 1 else {})}
                world["items"].append(item)
                if picture:                              # a cartoon station on the map, the picture in its card
                    self._attach_shot(item, picture, str(one.get("picture")))
                taken |= {(spot[0] + dx, spot[1] + dy) for dx in range(-1, sw + 1) for dy in range(-1, sh + 1)}
                laid += 1
        return laid

    def _thicken(self, world: Dict[str, Any], at: str) -> int:
        """EVERY TASK'S PLACES AS FULL AS ITS COMPLEXITY (fill_of): first the pictures shown again and again let go
        (_drop_repeated_pictures), then each place of a task filled to its share with a few large things
        (_thicken_one). How many things were laid."""
        self._drop_repeated_pictures(world)
        book = world.get("tasks") or {}
        laid = 0
        for zone in list(world["zones"]):
            task = str(zone.get("task") or "")
            record = book.get(task) or {}
            if not task or not record or zone.get("designed"):
                continue                                   # a room designed as a whole is not filled after
            want = record.get("fill")
            if want is None:                               # kept before its measures were: by its time alone
                want = fill_of(complexity_of({"hours": float(record.get("minutes") or 0) / 60,
                                              "days": len(record.get("days") or [])}))
            if float(want) > 0.05:
                laid += self._thicken_one(world, zone, float(want), at)
        return laid

    @staticmethod
    def _telling(one: Dict[str, Any]) -> Tuple[int, int]:
        """How much a thing tells, for the large ones: what the agent drew first, then the task's own picture as a
        statue or a banner, then a thing of the catalogue, then the rest."""
        if one.get("pixels"):
            return 0, 0
        if one.get("picture") and not (ITEM_KINDS.get(one["kind"]) or {}).get("wall"):
            return 1, 0
        if one["kind"] not in ITEM_KINDS or (ITEM_KINDS.get(one["kind"]) or {}).get("thing"):
            return 2, 0
        return 3, 0

    def _thicken_one(self, world: Dict[str, Any], zone: Dict[str, Any], want: float, at: str) -> int:
        """A PLACE FILLED WITH A FEW LARGE THINGS (the user, 2026-11-18: "你要把布局补满，可以是做一个item但是那个Item很大";
        "尽可能把有内容的东西放上去"): until `want` of its floor holds things, the things the model laid there made large
        - the most telling first (_telling), each design once before any again, a picture never twice - the largest
        that fits first, down to twice its size; a way kept round each, the door's way and every inner doorway clear.
        ONE PRINCIPLE (the user: "每次用一个Principal做design"): the place's own, as the model's designs there said -
        "consistency", its one most telling design made large again and again; "contrast" (else), its designs in
        turn, never the same one beside itself. Nothing of the code's own choosing: a place the model put nothing in
        is left as it is."""
        floor = self._floor_tiles(zone)
        if len(floor) < 9:
            return 0
        mine = [one for one in world["items"] if one["zone"] == zone["id"]]
        covered = set()
        for one in mine:
            if not (ITEM_KINDS.get(one["kind"]) or {}).get("wall"):
                covered |= self._tiles_of(one)
        target = int(want * len(floor) + 0.999)
        have = len(covered & floor)
        if have >= target:
            return 0
        designs, seen = [], set()
        for one in sorted(mine, key=lambda item: (self._telling(item), item.get("made", ""))):
            spec = ITEM_KINDS.get(one["kind"]) or {}
            if (not spec or spec.get("wall") or spec.get("under") or one["kind"] == "paint" or alive(one["kind"])
                    or one.get("carried") or one["kind"] in NOT_LARGE):
                continue
            key = one.get("group") or one["id"]
            if key not in seen:
                seen.add(key)
                designs.append(one)
        if not designs:
            return 0
        said = [one.get("principle") for one in mine if one.get("principle") in PRINCIPLES]
        principle = max(PRINCIPLES, key=said.count) if said else "contrast"
        xs, ys = [x for x, _y in floor], [y for _x, y in floor]
        left, top, right, bottom = min(xs), min(ys), max(xs) + 1, max(ys) + 1
        taken = covered | places.inner_walls(zone)
        if zone.get("plot"):
            taken |= self._blocked_in(world, zone)
        clear = set(places.inner_doorways(zone))
        if self._has_door(zone):
            clear |= {(zone["door"] + d, y) for d in range(DOOR_W) for y in range(top, bottom)}   # the door's way
        # A large thing stands off what stands there and off every way kept (the door's, the doorways', the way round
        # each large thing laid) - two large things share the way between them.
        laid, turn, used_pictures, ways, large = 0, 0, set(), set(clear), set()
        for one in mine:                                   # the large things laid before, and their ways round
            if int(one.get("scale") or 1) > 1:
                tiles = self._tiles_of(one)
                sw, sh = item_size(one)
                large |= tiles
                ways |= {(one["x"] + dx, one["y"] + dy) for dx in range(-1, sw + 1) for dy in range(-1, sh + 1)} - tiles
                if one.get("carried"):
                    used_pictures.add(next((m["id"] for m in designs if m.get("group") and m.get("group") == one.get("group")
                                            and (m.get("picture") or m.get("pixels"))), ""))
        ways &= floor
        whole = designs[:1] if principle == "consistency" else designs
        # FINE GRAIN ONLY (the user, 2026-11-18: "对这种大的东西，一定要细粒度"): the user's own pictures made large, the
        # largest first; then everything at its own size - what the agent drew large (pixel_art_huge, _wide) first.
        pictured = [one for one in whole if one.get("picture")]
        # At its own size a copy of a picture, or of what the agent drew, would be the same thing twice: not made.
        plain = [one for one in whole if not one.get("picture") and not one.get("pixels")]
        # INDOORS, NOTHING MADE LARGE (the user, 2026-11-18: "室内的东西的size就正常（比如床6格），你只能靠加布置"): a
        # room is filled by more furnishing at its own size (_fill_rows), never by furniture made larger.
        passes = [] if indoors(zone) else [(scale, pictured) for scale in range(SCALE_MOST, 1, -1) if pictured]
        for scale, pool in passes:
            for y in range(top, bottom):
                for x in range(left, right):
                    if have >= target:
                        return laid
                    for k in range(len(pool)):            # by the principle's turn: the next design that fits here
                        model = pool[(turn + k) % len(pool)]
                        if (model.get("picture") or model.get("pixels")) and model["id"] in used_pictures:
                            continue                     # a picture, or one the agent drew, made large once only
                        sw, sh = item_size({"kind": model["kind"], "scale": scale, "picture": model.get("picture")})
                        tiles = {(x + dx, y + dy) for dx in range(sw) for dy in range(sh)}
                        if not tiles <= floor or tiles & taken or tiles & ways:
                            continue
                        ring = {(x + dx, y + dy) for dx in range(-1, sw + 1) for dy in range(-1, sh + 1)} - tiles
                        if ring & large:
                            continue
                        one = {key: model[key] for key in ("kind", "name", "why", "group", "picture", "pixels", "print",
                                                           "print_colour") if model.get(key)}
                        one.update({"id": new_item_id(world), "zone": zone["id"], "x": x, "y": y, "from": "",
                                    "made": at, "principle": principle, "carried": True, **({"scale": scale} if scale > 1 else {})})
                        one.setdefault("picture", "")
                        world["items"].append(one)
                        taken |= tiles
                        large |= tiles
                        ways |= ring & floor                 # its way round kept
                        have += len(tiles)
                        laid += 1
                        turn += k + 1
                        if model.get("picture") or model.get("pixels"):
                            used_pictures.add(model["id"])
                        break
        if plain and have < target:
            laid += self._fill_rows(world, zone, floor, taken | ways, clear, plain, principle, target - have, at)
        return laid - self._unreached(world, zone, floor, left, top, right, bottom)

    @staticmethod
    def _has_door(zone: Dict[str, Any]) -> bool:
        """A building closed in front, entered by its door - not one open in front (the user, 2026-11-18: "你现在如果是
        开放式房间，不要中间留2个门的位置，很突兀"), a pavilion, a court, an open place or a project's ground."""
        return (not zone.get("plot") and zone.get("door") is not None and not ZONE_KINDS.get(zone["kind"], {}).get("outdoor")
                and (zone.get("form") or "closed") == "closed")

    def _unreached(self, world: Dict[str, Any], zone: Dict[str, Any], floor: set,
                   left: int, top: int, right: int, bottom: int) -> int:
        """WALKED TO, EVERY ONE: from the door (or the ground's edge) over the floor nothing stands on - each thing
        carried on that has no free floor beside it the walk reaches (walled in by what stood there before) let go.
        How many were."""
        items = [one for one in world["items"] if one["zone"] == zone["id"]]
        covered = set()
        for one in items:
            if not (ITEM_KINDS.get(one["kind"]) or {}).get("wall") and not (ITEM_KINDS.get(one["kind"]) or {}).get("under") \
                    and not one.get("on") and not one.get("hang"):
                covered |= self._tiles_of(one)
        free = floor - covered - places.inner_walls(zone)
        if self._has_door(zone):
            starts = [(zone["door"] + d, bottom - 1) for d in range(DOOR_W)]
        elif zone.get("plot") or ZONE_KINDS.get(zone["kind"], {}).get("outdoor"):
            starts = [(x, y) for (x, y) in free if x in (left, right - 1) or y in (top, bottom - 1)]
        else:
            starts = [(x, bottom - 1) for x in range(left, right)]            # open in front: all of its front
        reached, todo = set(), [one for one in starts if one in free]
        while todo:
            at = todo.pop()
            if at in reached or at not in free:
                continue
            reached.add(at)
            todo += [(at[0] + 1, at[1]), (at[0] - 1, at[1]), (at[0], at[1] + 1), (at[0], at[1] - 1)]
        dropped = [one for one in items if (one.get("carried") or one.get("designed")) and not one.get("on")
                   and not one.get("hang") and not (ITEM_KINDS.get(one["kind"]) or {}).get("wall")
                   and not (ITEM_KINDS.get(one["kind"]) or {}).get("under") and not any(
            (x + dx, y + dy) in reached for (x, y) in self._tiles_of(one) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
        gone_ids = {one["id"] for one in dropped}
        dropped += [one for one in items if one.get("on") and one["on"] in gone_ids]   # what was set on a thing let go
        if dropped:
            gone = {id(one) for one in dropped}
            world["items"] = [one for one in world["items"] if id(one) not in gone]
        return len(dropped)

    def _fill_rows(self, world: Dict[str, Any], zone: Dict[str, Any], floor: set, taken: set, clear: set,
                   designs: List[Dict[str, Any]], principle: str, want: int, at: str) -> int:
        """The rest at its own size, in rows a person walks between: one against the wall, a way, two back to back, a
        way, two... counted afresh between the walls across the room; the door's way up the room (its width at the
        door), a way across every THICK_ACROSS tiles from it, a way each side of a wall down the room and each inner
        doorway open to the nearest way. A design to each run of rows - in turn (contrast), or the one (consistency)."""
        xs, ys = [x for x, _y in floor], [y for _x, y in floor]
        left, top, w, h = min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        walls = places.inner_walls(zone)
        clear = set(clear)
        door = zone.get("door") if self._has_door(zone) else None
        if door is not None:
            clear |= {(door + d, y) for d in range(DOOR_W) for y in range(top + h - 2, top + h)}
            clear |= {(door, y) for y in range(top, top + h)}
        start = door if door is not None else left - 1
        clear |= {(x, y) for x in range(left, left + w) if (x - start) % THICK_ACROSS == 0 and abs(x - start) >= THICK_ACROSS
                  for y in range(top, top + h)}
        wall_cols = {x for (x, _y) in walls if sum(1 for (x2, _y2) in walls if x2 == x) > h // 2}
        clear |= {(x + d, y) for x in wall_cols for d in (-1, 1) for y in range(top, top + h)}
        wall_rows = sorted({y for (_x, y) in walls if sum(1 for (_x2, y2) in walls if y2 == y) > w // 2})
        runs, y0, ways = [], top, set()
        for stop in [*wall_rows, top + h]:
            base = y0 - 1 if y0 != top else y0
            things = [y for y in range(y0, stop) if (y - base) % 3 != 1]
            if len(things) >= 2 and things[-1] == stop - 1 and things[-2] == stop - 2:
                things.pop()                               # the last row, else with no way beside it
            ways |= set(range(y0, stop)) - set(things)
            for y in things:
                if runs and runs[-1][-1] == y - 1 and len(runs[-1]) < 2:
                    runs[-1].append(y)
                else:
                    runs.append([y])
            y0 = stop + 1
        for hy in wall_rows:                               # each doorway across open to the nearest way
            for gx in [x for x in range(left, left + w) if (x, hy) not in walls]:
                for step in (-1, 1):
                    y = hy + step
                    while top <= y < top + h and y not in ways and y not in wall_rows:
                        clear.add((gx, y))
                        y += step
        pool = designs[:1] if principle == "consistency" else designs
        laid, have = 0, 0
        bands = list(enumerate(runs))
        for band, rows in bands[::2] + bands[1::2]:       # every other run first: filled evenly, top and bottom
            # Its design in turn - or, too tall for this run of rows, the next that fits it.
            fitting = [pool[(band + k) % len(pool)] for k in range(len(pool))
                       if item_size({"kind": pool[(band + k) % len(pool)]["kind"]})[1] <= len(rows)]
            if not fitting:
                continue
            model = fitting[0]
            sw, sh = item_size({"kind": model["kind"]})
            for y in (rows[:1] if sh == 2 else rows):
                x = left
                while x + sw <= left + w and have < want:
                    tiles = {(x + dx, y + dy) for dx in range(sw) for dy in range(sh)}
                    if tiles <= floor and not tiles & taken and not tiles & clear:
                        one = {key: model[key] for key in ("kind", "name", "why", "group") if model.get(key)}
                        one.update({"id": new_item_id(world), "zone": zone["id"], "x": x, "y": y, "from": "",
                                    "made": at, "picture": "", "principle": principle, "carried": True})
                        world["items"].append(one)
                        taken |= tiles
                        have += len(tiles)
                        laid += 1
                        x += sw
                    else:
                        x += 1
            if have >= want:
                break
        return laid

    def _relayout(self, world: Dict[str, Any], zone: Dict[str, Any]) -> None:
        """EVERY THING IN A PLACE LAID OUT AGAIN in its new shape - in the order they were made; none ever dropped (one
        that finds no spot keeps where it was). What was carried on from the model's designs to fill it (_thicken) is
        let go: carried on again over the new shape at the growth's end."""
        mine = [one for one in world["items"] if one["zone"] == zone["id"] and not one.get("carried")]
        world["items"] = [one for one in world["items"] if one["zone"] != zone["id"]]
        for one in sorted(mine, key=lambda item: item.get("made", "")):
            spot = self._place(world, zone, one["kind"]) if one["kind"] in ITEM_KINDS else None
            if spot is not None:
                one["x"], one["y"] = spot
            world["items"].append(one)

    def _enlarge(self, world: Dict[str, Any], zone: Dict[str, Any], why: str, at: str) -> bool:
        """Larger: to its fullest within its cells first; then a cell more (two side by side, or one above the other),
        then four."""
        most = self._most(zone)
        if (zone["w"], zone["h"]) < most and self._resize(world, zone, most[0], most[1]):
            self._relayout(world, zone)
            zone["grown"].append({"at": at, "what": f"{zone['w']} x {zone['h']}", "why": why[:300]})
            return True
        span = places.span_of(zone)
        nexts = {(1, 1): [(2, 1), (1, 2)], (2, 1): [(2, 2)], (1, 2): [(2, 2)]}.get(span, [])
        outdoor = bool(ZONE_KINDS.get(zone["kind"], {}).get("outdoor"))
        for bigger in nexts:
            w, h = places.most_size(bigger, CELL_W, CELL_H, outdoor)
            if self._resize(world, zone, w, h, bigger):
                self._relayout(world, zone)
                zone["grown"].append({"at": at, "what": f"{bigger[0]} x {bigger[1]} scale, {zone['w']} x {zone['h']}",
                                      "why": why[:300]})
                return True
        return False

    def _redesign(self, world: Dict[str, Any], zone: Dict[str, Any], said: Dict[str, Any], at: str) -> bool:
        """REBUILT IN A NEW STYLE AND SIZE - a small room become a mansion - every thing in it kept, laid out again."""
        if said.get("form") in FORMS and not ZONE_KINDS.get(zone["kind"], {}).get("outdoor"):
            zone["form"] = str(said["form"])                # opened up, or closed, as it is rebuilt
        if said.get("shape") in shapes.OUTLINES:
            zone["shape"] = {"kind": str(said["shape"]), "seed": 0}
        style = str(said.get("style") or "").strip().lower()
        scale = str(said.get("scale") or "").strip().lower()
        outdoor = bool(ZONE_KINDS.get(zone["kind"], {}).get("outdoor"))
        if (style and style not in places.STYLES) or (scale and scale not in places.SCALES) or (not style and not scale):
            return False
        span = places.SCALES.get(scale) or places.span_of(zone)
        w, h = places.most_size(span, CELL_W, CELL_H, outdoor)
        if not self._resize(world, zone, w, h, span):
            return False
        if style and not outdoor:
            zone["style"] = style
        self._relayout(world, zone)
        zone["grown"].append({"at": at, "what": f"redesigned: {zone.get('style') or zone['kind']}, {span[0]} x {span[1]} scale",
                              "why": str(said.get("why") or "")[:300]})
        return True

#: A material, by its kind, as the things it may become in a task's place.
    MATERIAL_THINGS = {"file": ("bookshelf", "cabinet", "desk", "bookcase"), "page": ("painting", "map", "portrait"),
                       "message": ("wall_shelf", "lamp", "candle"), "picture": ("frame",)}

    def _fill_task_places(self, world: Dict[str, Any], at: str, days: Optional[Tuple[str, str]]) -> None:
        """A TASK'S PLACE NEVER BARE: when the model gave a task's place fewer things than the task's weight asks, the rest
        made from its materials - each named for the material and saying where it came from."""
        info = getattr(self, "_important_info", {}) or {}
        for zone in world["zones"]:
            task = info.get(str(zone.get("task") or ""))
            if not task or zone.get("grounds_of"):          # its grounds are the model's to furnish
                continue
            have = [one for one in world["items"] if one["zone"] == zone["id"]]
            used = " ".join(str(one.get("from") or "") for one in have)
            want = int(task.get("things") or 3) - len(have)
            pictures = {p["id"]: p for p in task.get("pictures") or []}
            for n, material in enumerate(task.get("materials") or []):
                if want <= 0:
                    break
                kind_of, _, title = material.partition(": ")
                if not title or title in used:
                    continue
                choices = self.MATERIAL_THINGS.get(kind_of, ("cabinet",))
                kind = choices[n % len(choices)]
                why = f"You worked with {title[:50]} in these days, so it is kept here, where the work is."
                item = self._add_item(world, zone, {"kind": kind, "name": title[:40], "why": why, "from": material}, at)
                if item is None:
                    break
                if days:
                    item["days"] = [days[0][:10], days[1][:10]]
                want -= 1
            # ITS PICTURES, EACH ONCE AND NONE ALIKE (2026-11-18: each growth had framed the same ones again - a
            # picture's id was looked for among the materials' words, where it never is): the most colourful first.
            framed = {str(one.get("picture_of") or "") for one in world["items"]} | {
                str(one.get("from") or "")[len("picture: "):] for one in world["items"]
                if str(one.get("from") or "").startswith("picture: ")}
            ranked = sorted(pictures.items(), key=lambda kv: -((picture_print(kv[1].get("path")) or (0, 0.0))[1]))
            for pid, picture in ranked:
                if want <= 0 or pid in framed or str(picture.get("what", "")) in framed:
                    continue
                if not self._picture_fits(world, zone, picture.get("path")):
                    continue
                item = self._add_item(world, zone, {"kind": "frame", "name": picture.get("what", "")[:40],
                                                    "why": f"A picture from that work, framed: {picture.get('what', '')[:60]}",
                                                    "from": f"picture: {picture.get('what', '')}"}, at)
                if item is None:
                    break
                self._attach_shot(item, picture, pid)    # a cartoon frame on the map, the picture in its card
                framed.add(pid)
                want -= 1
        self._important_info = {}

    def _decorate(self, world: Dict[str, Any], said: Any, offered: List[Dict[str, Any]],
                  by_name: Dict[str, Dict[str, Any]], at: str) -> None:
        """A PLACE'S FLOOR MADE ITS PROJECT'S: its name spelled large, its logo laid down, a picture the agent drew - and a
        task's own name put back in a place's name that lacks it. Each checked; the rest ignored."""
        if not isinstance(said, list):
            return
        pictures = {str(one["id"]): one for one in offered if one.get("id")}
        by_id = {one["id"]: one for one in world["zones"]}
        for one in said[:12]:
            if not isinstance(one, dict):
                continue
            where = str(one.get("zone") or "")
            zone = by_id.get(where) or by_name.get(where.lower())
            if zone is None:
                continue
            # A place there before, made a task's own (so its sign and name can carry the task's name).
            task = str(one.get("task") or "")
            info = (getattr(self, "_important_info", {}) or {}).get(task)
            if info and not zone.get("task") and not any(z.get("task") == task for z in world["zones"]):
                zone["task"], zone["task_title"] = task, str(info.get("title") or "")[:80]
            if one.get("on") in ("wall", "floor"):
                zone["art_on"] = str(one["on"])
            text = " ".join(str(one.get("floor_text") or "").split())[:12]
            if text:
                zone["floor_text"] = text
                colour = str(one.get("floor_color") or "")
                zone["floor_color"] = colour if re.fullmatch(r"#[0-9a-fA-F]{6}", colour) else "#7a4fa0"
            picture = str(one.get("floor_picture") or "")
            if picture in pictures:
                kept = self._copy_picture(pictures[picture], f"floor-{zone['id']}")
                if kept:
                    zone["floor_picture"] = kept
            mural = one.get("mural")
            if isinstance(mural, dict):
                palette = [c for c in (mural.get("palette") or []) if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c)][:8]
                rows = [re.sub(r"[^0-7.]", ".", str(r))[:24] for r in (mural.get("rows") or [])][:16]
                if palette and rows and any(ch != "." for r in rows for ch in r):
                    zone["mural"] = {"palette": palette, "rows": rows}
            name = " ".join(str(one.get("name") or "").split())[:60]
            title = str(zone.get("task_title") or "")
            if name and title and title.lower() in name.lower():
                zone["grown"].append({"at": at, "what": f"renamed: {zone['name']} -> {name}", "why": "its task's own name"})
                zone["name"] = name

    def _after(self, world: Dict[str, Any], kind: str, began: Optional[List[str]]) -> Optional[Tuple[float, float]]:
        """WHERE A PLACE GOES ON THE ROAD OF THEIR LIFE (2026-11-17: "这个在选layout也要考虑"): after the places it follows
        in time (playplaces.life_road) - a step on from them, away from its region's heart, so the road goes on
        outwards and what began together branches from one place. None: nothing before it, or not on the road."""
        import math
        if not began or kind in places.NATURAL:
            return None
        trial = {"id": "~new", "kind": kind, "days": began, "made": ""}
        follows = places.life_road(world, [*world["zones"], trial]).get("~new") or []
        by_id = {one["id"]: one for one in world["zones"]}
        before = [by_id[i] for i in follows if i in by_id]
        if not before:
            return None
        centres = [places.centre(places.zone_rect(one)) for one in before]
        cx, cy = sum(c[0] for c in centres) / len(centres), sum(c[1] for c in centres) / len(centres)
        region = next((r for r in world["regions"] if r["id"] == before[-1].get("region")), world["regions"][-1])
        hx, hy = places.heart(world, region)
        dx, dy = cx - hx, cy - hy
        far = math.hypot(dx, dy)
        if far < 1:                                   # at the heart: on, by chance, in one of four ways
            angle = places._seeded(str(began[0]), len(world["zones"]), 0) * 2 * math.pi
            dx, dy, far = math.cos(angle), math.sin(angle), 1.0
        step = 16
        return cx + dx / far * step, cy + dy / far * step

    #: Projects whose places stand within this many tiles of each other are neighbours; two or three to a neighbourhood.
    NEIGHBOURS_WITHIN, NEIGHBOURS_MOST = 30, 3

    #: A place with fewer things than one for each SPARSE_TILES tiles of its floor is "sparse": filled first.
    SPARSE_TILES = 14

    def _sparse(self, world: Dict[str, Any], zone: Dict[str, Any]) -> bool:
        if zone.get("kind") in places.NATURAL:
            return False
        things = sum(1 for one in world["items"] if one["zone"] == zone["id"])
        return things * self.SPARSE_TILES < zone.get("w", 0) * zone.get("h", 0)

    #: How far beyond B the place going on from A to B is drawn towards.
    GOES_ON_STEP, LINES_MOST = 16, 3

    def _lines(self, world: Dict[str, Any]) -> List[Dict[str, Any]]:
        """WHERE THE ROAD GOES ON (the user: "place A -> B, -> C 基本在同一个线上"): a place B that follows one place A on
        the road of their life, nothing after B yet, no place going on from them made - each with what A and B are and
        the point beyond B, on the line from A through it."""
        import math
        follows = places.life_road(world)
        after = {i for ids in follows.values() for i in ids}
        made = {tuple(one.get("goes_on") or []) for one in world["zones"] if one.get("goes_on")}
        by_id = {one["id"]: one for one in world["zones"]}
        out = []
        for b, ids in follows.items():
            if len(ids) != 1 or b in after or (ids[0], b) in made or ids[0] not in by_id or b not in by_id:
                continue
            za, zb = by_id[ids[0]], by_id[b]
            (ax, ay), (bx, by) = places.centre(places.zone_rect(za)), places.centre(places.zone_rect(zb))
            far = math.hypot(bx - ax, by - ay) or 1.0
            said = lambda z: {"name": z["name"], "kind": z["kind"], "style": z.get("style", ""),  # noqa: E731
                              **({"task": z["task_title"]} if z.get("task_title") else {})}
            out.append({"zones": [za["id"], zb["id"]], "from": said(za), "to": said(zb),
                        "beyond": (bx + (bx - ax) / far * self.GOES_ON_STEP, by + (by - ay) / far * self.GOES_ON_STEP)})
        return out[-self.LINES_MOST:]

    def _neighbourhoods(self, world: Dict[str, Any]) -> List[Dict[str, Any]]:
        """PROJECTS STANDING CLOSE, WITH NOTHING BETWEEN THEM YET: the main places of tasks, each with its nearest
        others (two or three together), not already joined by a place between them."""
        import math
        mains = [one for one in world["zones"] if one.get("task") and not one.get("grounds_of")]
        joined = {tuple(sorted(one.get("between") or [])) for one in world["zones"] if one.get("between")}
        centre = {one["id"]: places.centre(places.zone_rect(one)) for one in mains}
        seen, out = set(), []
        for one in mains:
            cx, cy = centre[one["id"]]
            close = sorted((math.hypot(centre[o["id"]][0] - cx, centre[o["id"]][1] - cy), o["id"]) for o in mains
                           if o["id"] != one["id"] and o.get("region") == one.get("region"))
            group = [one["id"]] + [ident for d, ident in close if d <= self.NEIGHBOURS_WITHIN][:self.NEIGHBOURS_MOST - 1]
            key = tuple(sorted(group))
            if len(group) < 2 or key in seen or key in joined:
                continue
            seen.add(key)
            by_id = {z["id"]: z for z in mains}
            xs, ys = [centre[i][0] for i in key], [centre[i][1] for i in key]
            out.append({"zones": list(key), "projects": [by_id[i].get("task_title") or by_id[i]["name"] for i in key],
                        "middle": (sum(xs) / len(xs), sum(ys) / len(ys))})
        return out[:4]

    def drawn_days(self, world: Dict[str, Any]) -> int:
        """How many days of the user's life the world has been drawn from so far."""
        first = str(world.get("drawn_from") or "")
        if not first:
            first = next((str(one["days"][0]) for one in world.get("growths") or [] if one.get("days")),
                         str(world.get("born") or ""))
        try:
            return max(0, (datetime.fromisoformat(str(world.get("caught_up") or "")[:10])
                           - datetime.fromisoformat(first[:10])).days)
        except ValueError:
            return 0

    def _open_region(self, world: Dict[str, Any], said: Any, at: str,
                     days: Optional[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
        """A NEW REGION, when one may open now and the model chose each of its settings from the lists."""
        mood = getattr(self, "_mood_now", None)
        by_feeling = bool(mood) and bool(getattr(self, "_mood_opens", False))
        if not by_feeling and (not isinstance(said, dict) or not regions.may_leave(world, self.drawn_days(world))):
            return None
        said = dict(said) if isinstance(said, dict) else {}
        if mood:
            # ITS SCENE IS ITS FEELING'S, AND ITS WEATHER WITH IT (the user: "场景要和天气搭配，比如雪山就是雪天"): the
            # code's, whatever else was said; what the feeling does not set, said by the model - or the land before's.
            said.update(muse.scene_settings(mood["scene"]))
            before = world["regions"][-1]
            for key, allowed in regions.SETTINGS.items():
                if str(said.get(key) or "").strip().lower() not in allowed:
                    said[key] = before.get(key) if before.get(key) in allowed else regions.STARTER[key]
            said["name"] = said.get("name") or mood.get("land_name")
            said["why"] = said.get("why") or mood.get("why")
        style = regions.chosen(said)
        name = " ".join(str(said.get("name") or "").split())[:60]
        if style is None or not name:
            return None
        region = regions.open_region(world, style, name, str(said.get("why") or ""), at, days, CELL_W, CELL_H,
                                     travel=bool(said.get("travel")))
        if mood and by_feeling:
            self._land_own(region, mood["scene"])
        if region.get("travel"):
            world.setdefault("middles", {})[regions.area_of(region)] = region["id"]
        music = tune_of(said.get("music"))
        if music:
            region["music"] = music
        if mood:
            region["scene"], region["mood"] = mood["scene"], mood.get("mood", "")
            region.update({k: mood[k] for k in ("feeling", "to_them", "look", "shape") if mood.get(k)})
            if mood.get("music"):                       # the feeling's music, over what was said for the land
                region["music"] = mood["music"]
            self._scene_life(region, mood)
            nature = muse.MOOD_SCENES[mood["scene"]].get("nature")
            if nature in ZONE_KINDS:                    # its mountain, its volcano, its peak - where it can stand
                made = self._add_zone(world, nature, mood.get("land_name") or nature, mood.get("why") or "", at, region=region)
                if made is not None and mood.get("music"):
                    made["music"] = mood["music"]
        road = muse.road_of(said.get("road"))
        if road and not region.get("road"):             # its ways, as the growth designed them
            region["road"] = road
        caught = catch_of(said.get("catch"))
        if caught:
            region["catch"] = caught
        return region

    def _copy_picture(self, offered: Dict[str, Any], item_id: str) -> str:
        """A small copy of the user's picture, kept with the thing - its file name ("" when it could not be)."""
        source = Path(str(offered.get("path") or ""))
        if not source.is_file():
            return ""
        target = self.folder / "pictures" / f"{item_id}.jpg"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            from PIL import Image
            with Image.open(source) as image:
                image = image.convert("RGB")
                image.thumbnail((PICTURE_COPY_SIDE, PICTURE_COPY_SIDE))
                image.save(target, "JPEG", quality=82)
        except Exception:
            try:
                shutil.copyfile(source, target)
            except OSError:
                return ""
        return target.name

    def _attach_shot(self, item: Dict[str, Any], offered: Dict[str, Any], picture_id: str) -> None:
        """A THING AND ITS SCREENSHOT (the user, 2026-11-19: "item本身显示的不是图片，但是你打开的细节会展示图片（也就是item会和
        截图connect起来)"; "在地图上所有图片尽可能是像素风，真实图片都藏在细节里"): the thing drawn as itself, the picture
        it comes from kept with it - a small copy shown in its card, the original opened from there."""
        name = self._copy_picture(offered, f"shot-{item['id']}")
        if not name:
            return
        item["shot"], item["shot_of"] = name, picture_id
        item["shot_source"] = str(offered.get("path") or "")
        item["shot_what"] = " ".join(str(offered.get("what") or "").split())[:160]
        self._keep_print(item, offered.get("path"))

    @staticmethod
    def routes(world: Dict[str, Any]) -> Dict[str, Any]:
        """THE WAY THROUGH, AS IT WAS LIVED (the user, 2026-11-20: "agent要在室内和室外都有动线的概念，有箭头之类的标志，整体
        来说，跟着箭头/路牌走能够带user重走user在时序上对这些project的经历"; "包括一个项目内部发生的progress"): for each
        place, the things on its way in the order the work happened - the cards' things by when each was first worked
        on (then the tree's order), its story's stations by their step - from its door; for each project's great
        ground, its parts by when each began. Between places, the road of their life (play-regions.js playLifeRoad)
        joins them in time. {"rooms": {zone id: [item ids]}, "grounds": {zone id: [zone ids]}} - two stops at least.
        The page lays arrows along each and a numbered sign by each stop."""
        cards: Dict[str, Tuple[str, int]] = {}
        for tree in (world.get("work_trees") or {}).values():
            for n, card in enumerate(tree or []):
                cards[str(card.get("id"))] = (str(card.get("first") or "9999"), n)
        rooms: Dict[str, List[str]] = {}
        by_zone: Dict[str, List[Tuple[Tuple[str, int, str], str]]] = {}
        for item in world.get("items") or []:
            key = None
            if item.get("card") and str(item["card"]) in cards:
                first, n = cards[str(item["card"])]
                key = (first, n, str(item.get("id")))
            elif item.get("story_step"):
                key = (str(item["story_step"]), 0, str(item.get("id")))
            if key is not None:
                by_zone.setdefault(str(item.get("zone")), []).append((key, str(item["id"])))
        for zone, stops in by_zone.items():
            seen, order = set(), []
            for key, ident in sorted(stops):
                card = next((i.get("card") for i in world["items"] if str(i.get("id")) == ident), None)
                if card and card in seen:                 # a card shown by several things: its first on the way
                    continue
                seen.add(card)
                order.append(ident)
            if len(order) >= 2:
                rooms[zone] = order[:ROUTE_STOPS_MOST]
        grounds: Dict[str, List[str]] = {}
        for ground in [z for z in world.get("zones") or [] if z.get("great")]:
            parts = [z for z in world["zones"] if z.get("in_great") == ground["great"]]
            parts.sort(key=lambda z: (str((z.get("days") or ["9999"])[0]), str(z.get("made") or "")))
            if len(parts) >= 2:
                grounds[ground["id"]] = [z["id"] for z in parts]
        return {"rooms": rooms, "grounds": grounds}

    def original(self, ident: str) -> Optional[Tuple[bytes, str]]:
        """A thing's picture as it was, whole - its screenshot ("shot-<id>") or the picture it shows ("<id>"): the bytes
        and their type. Only a picture the world keeps with one of its things."""
        shot = str(ident).startswith("shot-")
        wanted = str(ident)[5:] if shot else str(ident)
        one = next((i for i in (self.load() or {}).get("items") or [] if str(i.get("id")) == wanted), None)
        source = Path(str((one or {}).get("shot_source" if shot else "picture_source") or ""))
        types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                 ".gif": "image/gif", ".bmp": "image/bmp"}
        if one is None or source.suffix.lower() not in types or not source.is_file():
            return None
        try:
            return source.read_bytes(), types[source.suffix.lower()]
        except OSError:
            return None

    def picture(self, item_id: str) -> Optional[bytes]:
        safe = re.sub(r"[^A-Za-z0-9_-]", "", item_id)
        path = self.folder / "pictures" / f"{safe}.jpg"
        try:
            return path.read_bytes() if safe and path.is_file() else None
        except OSError:
            return None

    # -- the day's growth, by itself ----------------------------------------------------------------------------------
    def on(self) -> bool:
        from ..common import modules
        return modules.enabled(self.config, "play_world")

    def due(self, now: Optional[datetime] = None) -> bool:
        """Once the user has opened the world (it exists): while behind, each look; caught up, once a day (their own
        day)."""
        world = self.load()
        if world is None or not self.on():
            return False
        now = now or datetime.now(timezone.utc)
        if self.behind(world, now.isoformat(timespec="seconds")):
            return True
        last = str(world.get("grown_at") or "")
        if not last:
            return True
        try:
            then = datetime.fromisoformat(last)
        except ValueError:
            return True
        return then.astimezone().date() < now.astimezone().date()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, name="play-world", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _watch(self) -> None:
        """CAUGHT UP WITHOUT WAITING (the user, 2026-11-18: "还没追上时，画完一段马上接着画下一段，不再停 5 分钟 ... 是的";
        "重启后也立刻检查，不用先等 5 分钟。 也是的"): looked at START_SECONDS after the agent starts; behind, the next
        part drawn CHAIN_SECONDS after one is drawn; a part that failed waits BEHIND_SECONDS, as before; caught up, a
        look each LOOK_SECONDS."""
        pause: Optional[float] = START_SECONDS
        while not self._stop.wait(pause if pause is not None else (BEHIND_SECONDS if self.behind() else LOOK_SECONDS)):
            pause = None
            try:
                if self.due():
                    said = self.grow()
                    log.info("the play world grew: %s", said)
                    if isinstance(said, dict) and not said.get("error") and self.behind():
                        pause = CHAIN_SECONDS                # the next part at once
            except Exception:
                log.info("the play world's growth failed", exc_info=True)

    def paused(self) -> bool:
        """DRAWING PAUSED (the user, 2026-11-20: "debugger 版的代码，play加一个暂停框，是暂停drawing"): set from the Play
        view's pause button, in a build that carries the Debugger - nothing grows, no model is asked, till it is
        resumed; the world is there to walk in all the while."""
        try:
            # ONLY IN THE DEBUGGER'S BUILD (the user, 2026-11-20: "这个设置只在debugger里生效"): an installed build - no
            # button to resume - grows whatever was left set.
            from ..build_flags import debugger_available
            if not (debugger_available() and bool(self.config.get("debugger_enabled", True))):
                return False
            return bool(self.config.get("play_world_paused", False))
        except Exception:
            return False

    def pause(self, paused: bool) -> bool:
        """Drawing paused, or resumed (kept as a setting of the app's own). Whether it is paused now."""
        try:
            self.config.set("play_world_paused", bool(paused), internal=True)
        except Exception:
            log.info("the play world's pause could not be kept", exc_info=True)
        if not paused and self.on() and self.behind():
            self.grow_soon()                             # resumed: on from where it was
        return self.paused()

    def grow_soon(self) -> bool:
        """Grown now, off the caller's thread (the world opened the first time, or the user asked) - AND ON, PART AFTER
        PART, TILL IT IS CAUGHT UP (the user, 2026-11-19: "这个是停滞了吗，怎么没有adding more了": it drew one part and
        stopped, the watch asleep for half an hour). Whether begun."""
        if self._growing.locked():
            return False

        def run() -> None:
            for _part in range(CHAIN_PARTS_MOST):
                said = self.grow()
                log.info("the play world grew: %s", said)
                if not isinstance(said, dict) or said.get("error") or not self.behind() or self._stop.wait(CHAIN_SECONDS):
                    break
        threading.Thread(target=run, name="play-world-grow", daemon=True).start()
        return True


def lived_between(db: Any, since: str, until: str, most: int = 40) -> str:
    """WHAT THE USER LIVED BETWEEN TWO TIMES - MAINLY AS THE AGENT KNOWS IT (the user, 2026-11-17: "建图的输入改成以 agent
    的任务、状态、项目为主"): their projects, their tasks (what each is, its goal, what they were doing), what the agent saw
    them doing (its states, spread over the days); then, shorter, what they said, read, had to do and had on their
    calendar."""
    parts: List[str] = []

    def rows(sql: str, *args: Any) -> List[Any]:
        try:
            return list(db.query(sql, args))
        except Exception:
            return []

    def short(text: Any, n: int) -> str:
        return " ".join(str(text or "").split())[:n]
    projects = rows("SELECT name, why FROM state_projects WHERE scope = 'live' AND name != '' AND updated_at >= ? "
                    "AND created_at < ? ORDER BY updated_at DESC LIMIT 12", since, until)
    if projects:
        parts.append("THEIR PROJECTS:\n" + "\n".join(f"- {short(one['name'], 120)}: {short(one['why'], 220)}"
                                                       for one in projects))
    tasks = []
    for one in rows("SELECT described, updated_at FROM state_tasks WHERE updated_at >= ? AND created_at < ? "
                    "AND (retired_at IS NULL OR retired_at >= ?) ORDER BY updated_at DESC LIMIT 60", since, until, since):
        try:
            said = json.loads(one["described"] or "{}")
        except ValueError:
            continue
        title = short(said.get("title"), 100)
        if not title:
            continue
        more = "; ".join(f"{key}: {short(said.get(key), 140)}" for key in ("summary", "goal", "doing") if said.get(key))
        tasks.append(f"- {title}" + (f" ({more})" if more else ""))
    if tasks:
        parts.append("THEIR TASKS:\n" + "\n".join(tasks[:30]))
    states = rows("SELECT created_at, title FROM states WHERE created_at >= ? AND created_at < ? AND about = 'user' "
                  "ORDER BY created_at", since, until)
    if states:                                          # spread over the days, not the last few hours
        step = max(1, len(states) // most)
        parts.append("WHAT THEY WERE DOING (as the agent saw it):\n" + "\n".join(
            f"- {str(one['created_at'])[:10]} {short(one['title'], 110)}" for one in states[::step][:most]))
    said = rows("SELECT created_at, content FROM messages WHERE role = 'user' AND created_at >= ? AND created_at < ? "
                "ORDER BY created_at LIMIT ?", since, until, 20)
    if said:
        parts.append("THEY SAID (in their chats):\n" + "\n".join(
            f"- {str(one['created_at'])[:10]} {short(one['content'], 160)}" for one in said))
    read = rows("SELECT first_at, title, kind, seconds FROM reading_pieces WHERE first_at >= ? AND first_at < ? "
                "ORDER BY seconds DESC LIMIT 15", since, until)
    if read:
        parts.append("THEY READ OR WATCHED:\n" + "\n".join(
            f"- {str(one['first_at'])[:10]} {short(one['title'], 120)} ({one['kind']}, {int(one['seconds'] or 0) // 60} min)"
            for one in read))
    todos = rows("SELECT created_at, text, done_at FROM todo_items WHERE created_at >= ? AND created_at < ? "
                 "AND deleted_at = '' ORDER BY created_at LIMIT 20", since, until)
    if todos:
        parts.append("THEIR TO-DOS:\n" + "\n".join(
            f"- {str(one['created_at'])[:10]} {short(one['text'], 120)}{' (done)' if one['done_at'] else ''}" for one in todos))
    events = rows("SELECT scheduled_at, title FROM scheduled_tasks WHERE scheduled_at >= ? AND scheduled_at < ? "
                  "ORDER BY scheduled_at LIMIT 20", since, until)
    if events:
        parts.append("THEIR CALENDAR:\n" + "\n".join(f"- {str(one['scheduled_at'])[:16]} {short(one['title'], 120)}"
                                                       for one in events))
    return "\n\n".join(parts)


#: A search of the agent's own records: at most this many questions, finds each, characters in all.
LOOK_MOST, LOOK_FINDS, LOOK_CHARS = 5, 6, 4000


def look_up(db: Any, memory_root: Optional[Path], asked: List[str], until: str) -> str:
    """THE AGENT'S OWN RECORDS SEARCHED (the user, 2026-11-17: "允许它画之前先搜一轮" - "A": its records only, never the
    user's files nor the web): for each question, the best matches by its words in the chats, the memory files, what
    was read, the states, the tasks, the projects, the to-dos and the calendar - up to the days drawn."""
    import re as _re
    out: List[str] = []

    def rows(sql: str, *args: Any) -> List[Any]:
        try:
            return list(db.query(sql, args))
        except Exception:
            return []
    memories = []
    if memory_root is not None:
        try:
            memories = [(one.stem, one.read_text(encoding="utf-8")[:4000]) for one in Path(memory_root).glob("*.md")]
        except OSError:
            memories = []
    for question in [str(one) for one in asked if str(one).strip()][:LOOK_MOST]:
        words = [w for w in _re.findall(r"[\w\u4e00-\u9fff]+", question.lower()) if len(w) >= 2][:6]
        if not words:
            continue
        like = " OR ".join(["{col} LIKE ?"] * len(words))
        args = [f"%{w}%" for w in words]
        found: List[Tuple[int, str]] = []

        def take(kind: str, sql: str, col: str, text_of: Callable[[Any], str], *extra: Any) -> None:
            for one in rows(sql.format(where=like.format(col=col)), *args, *extra):
                text = " ".join(str(text_of(one) or "").split())
                score = sum(1 for w in words if w in text.lower())
                if score:
                    found.append((score, f"[{kind}] {text[:240]}"))
        take("chat", "SELECT created_at, content FROM messages WHERE ({where}) AND created_at < ? LIMIT 40", "content",
             lambda r: f"{str(r['created_at'])[:10]} {r['content']}", until)
        take("read", "SELECT first_at, title, summary FROM reading_pieces WHERE ({where}) AND first_at < ? LIMIT 20",
             "title || ' ' || summary", lambda r: f"{str(r['first_at'])[:10]} {r['title']} - {r['summary']}", until)
        take("state", "SELECT created_at, title, detail FROM states WHERE ({where}) AND created_at < ? LIMIT 30",
             "title || ' ' || detail", lambda r: f"{str(r['created_at'])[:10]} {r['title']} - {r['detail']}", until)
        take("task", "SELECT described FROM state_tasks WHERE ({where}) AND created_at < ? LIMIT 15", "described",
             lambda r: _task_line(r["described"]), until)
        take("project", "SELECT name, why FROM state_projects WHERE ({where}) LIMIT 10", "name || ' ' || why",
             lambda r: f"{r['name']}: {r['why']}")
        take("to-do", "SELECT created_at, text FROM todo_items WHERE ({where}) AND created_at < ? LIMIT 10", "text",
             lambda r: f"{str(r['created_at'])[:10]} {r['text']}", until)
        take("calendar", "SELECT scheduled_at, title FROM scheduled_tasks WHERE ({where}) AND scheduled_at < ? LIMIT 10",
             "title", lambda r: f"{str(r['scheduled_at'])[:16]} {r['title']}", until)
        for name, text in memories:
            low = text.lower()
            score = sum(1 for w in words if w in low)
            if score:
                at = min(low.find(w) for w in words if w in low)
                found.append((score, f"[memory {name}] {' '.join(text[max(0, at - 80):at + 200].split())}"))
        found.sort(key=lambda one: -one[0])
        if found:
            out.append(f"Q: {question}\n" + "\n".join(f"  {line}" for _s, line in found[:LOOK_FINDS]))
        else:
            out.append(f"Q: {question}\n  (nothing found)")
    return "\n".join(out)[:LOOK_CHARS]


#: Words that mark a taste in what the agent keeps of the user.
TASTE_WORDS = ("prefer", "like", "love", "hate", "dislike", "style", "taste", "colour", "color", "favourite",
               "favorite", "喜欢", "偏好", "讨厌", "风格", "颜色", "不要", "爱", "审美")


#: Their own words of taste, from their chats: at most this many sentences, the latest.
TASTES_SAID_MOST = 25


def tastes_of(memory_root: Optional[Path], db: Any = None) -> str:
    """WHAT THE USER LIKES AND DISLIKES (the user, 2026-11-17: "你的设计也可以retrieve 用户的preference来决定你设计的
    风格"; "加上" - their own words from their chats too): the sentences of their messages that say a taste, the latest
    first; the agent's memory index, and the lines of its memory files that say one."""
    out: List[str] = []
    if db is not None:
        said: List[str] = []
        likes = " OR ".join(["content LIKE ?"] * len(TASTE_WORDS))
        try:
            found = list(db.query(f"SELECT created_at, content FROM messages WHERE role = 'user' AND ({likes}) "
                                  "ORDER BY created_at DESC LIMIT 200", tuple(f"%{w}%" for w in TASTE_WORDS)))
        except Exception:
            found = []
        for one in found:
            for sentence in re.split(r"(?<=[。！？!?\n])|(?<=\. )", str(one["content"] or "")):
                sentence = " ".join(sentence.split())
                if len(sentence) > 4 and any(w in sentence.lower() for w in TASTE_WORDS) and sentence not in said:
                    said.append(sentence[:200])
                if len(said) >= TASTES_SAID_MOST:
                    break
            if len(said) >= TASTES_SAID_MOST:
                break
        if said:
            out.append("THEIR OWN WORDS OF TASTE (from their chats, latest first):\n" + "\n".join(f"- {s}" for s in said))
    if memory_root is None:
        return "\n\n".join(out)
    root = Path(memory_root)
    try:
        index = (root / "MEMORY.md").read_text(encoding="utf-8")[:1200].strip()
    except OSError:
        index = ""
    if index:
        out.append("WHAT THE AGENT KEEPS OF THEM (index):\n" + index)
    lines: List[str] = []
    try:
        files = sorted(root.glob("*.md"))
    except OSError:
        files = []
    for one in files:
        if one.name == "MEMORY.md":
            continue
        try:
            text = one.read_text(encoding="utf-8")[:6000]
        except OSError:
            continue
        for line in text.splitlines():
            low = line.lower()
            if any(word in low for word in TASTE_WORDS) and len(line.strip()) > 4:
                lines.append(f"- {' '.join(line.split())[:200]}")
    if lines:
        out.append("THEIR TASTES (from its notes):\n" + "\n".join(lines[:20]))
    return "\n\n".join(out)


#: The important tasks of the days drawn: at most this many; materials named for each, pictures offered for each.
MATERIALS_MOST, TASK_PICTURES_MOST = 12, 3
#: A task's sources at most - files opened and pages read, where a picture may be found (agent/playfind.py).
SOURCES_MOST = 10
#: A task takes this many minutes of their activity in the days drawn to be given a place; no count fixed. One of less
#: but at least SMALL_MINUTES is a small task: grouped with others like it into one whole scene.
TASK_MINUTES, SMALL_MINUTES = 60, 20
#: HOW OPEN A BUILDING IS (the user, 2026-11-18: "需要有一些半开放的设计"): walled round; open in front (a hall, its whole
#: front a row of pillars); round a court (its wings and back, the front open on a courtyard); a pavilion (a roof on
#: pillars, no walls) - api/web/play-regions.js lays each.
FORMS = ("closed", "open_front", "u_court", "pavilion")
#: How a place's sign may look (api/web/play.js PLAY_SIGN_STYLES).
SIGN_STYLES = ("wood", "stone", "metal", "scroll", "neon", "gold", "red", "pink")


def _score_minutes(said: Dict[str, Any], db: Any, members: List[str]) -> float:
    return float(said.get("minutes") or 0) if "minutes" in said else _states_minutes(db, members)


def _states_day_minutes(db: Any, members: List[str]) -> Dict[str, float]:
    """The time a task took each day (the user's own days): each state's minutes - its own, else its span no more than
    SPAN_ONLY_MOST - on the day it began; never more on a day than the time its states covered that day."""
    from datetime import datetime
    ids = [str(one) for one in members if one][-900:]
    if not ids:
        return {}
    try:
        rows = db.query(f"SELECT metadata, starts_at, ends_at FROM states WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
    except Exception:
        return {}
    said_by_day: Dict[str, float] = {}
    spans: Dict[str, List[Tuple[float, float]]] = {}
    for row in rows:
        try:
            began = datetime.fromisoformat(str(row["starts_at"]).replace("Z", "+00:00"))
            ended = datetime.fromisoformat(str(row["ends_at"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        try:
            said = max(0.0, float((json.loads(row["metadata"] or "{}") or {}).get("minutes") or 0.0))
        except (TypeError, ValueError):
            said = 0.0
        span = max(0.0, (ended - began).total_seconds() / 60)
        said = min(said, span) if said else min(span, SPAN_ONLY_MOST)
        day = began.astimezone().strftime("%Y-%m-%d")
        said_by_day[day] = said_by_day.get(day, 0.0) + said
        spans.setdefault(day, []).append((began.timestamp() / 60, began.timestamp() / 60 + said))
    out = {}
    for day, total in said_by_day.items():
        covered, end = 0.0, None
        for a, b in sorted(spans[day]):
            if end is None or a > end:
                covered, end = covered + b - a, b
            elif b > end:
                covered, end = covered + b - end, b
        out[day] = round(min(total, covered), 1)
    return {d: out[d] for d in sorted(out)}


#: The pets and the animals of the fields and woods: never placed as still things (the user, 2026-11-18: "要是动画item放上去
#: 不动，那你还是别放会动的item") - a land's few walk about on their own (api/web/play-act.js). A zoo's beasts and the sea's
#: fish stay ("这些不要移除"), and the statues, the robots, a wisp.
FIELD_ANIMALS = {"cat", "dog", "rabbit", "squirrel", "chicken", "cow", "sheep", "goat", "deer", "owl", "eagle"}


def alive(kind: str) -> bool:
    """Whether a kind is one of the animals that only walk about, never stand still."""
    return kind in FIELD_ANIMALS


def task_lineage(db: Any) -> Dict[str, Dict[str, Any]]:
    """What each task was born of (state_tasks.born_of): joined into another, merged from some, split from one - and
    whether it was retired since ("retired": a merge undone within the hour, 2026-10-17, is no join to follow)."""
    try:
        rows = list(db.query("SELECT id, born_of, retired_at FROM state_tasks WHERE scope = 'live'"))
    except Exception:
        return {}
    out = {}
    for row in rows:
        try:
            born = json.loads(row["born_of"] or "{}")
        except ValueError:
            continue
        if isinstance(born, dict):
            out[str(row["id"])] = {**born, **({"retired": True} if row["retired_at"] else {})}
    return out


def _project_day_minutes(db: Any, project_id: str) -> Dict[str, float]:
    """A PROJECT'S TIME EACH DAY, ALL OF IT (2026-11-18: its row lists only its last 600 states - Personal Agent's
    reached back 12 days of its 37): from the run tasks it is made of (state_run_tasks, kept whole), each one's minutes
    shared over the days (the user's own) its time ran in. {} when it has none - not a project, or from before them."""
    from datetime import datetime, timedelta
    try:
        rows = list(db.query("SELECT minutes, starts_at, ends_at FROM state_run_tasks WHERE project_id = ? AND scope = 'live'",
                             (project_id,)))
    except Exception:
        return {}
    out: Dict[str, float] = {}
    for row in rows:
        try:
            began = datetime.fromisoformat(str(row["starts_at"]).replace("Z", "+00:00")).astimezone()
            ended = datetime.fromisoformat(str(row["ends_at"]).replace("Z", "+00:00")).astimezone()
            minutes = max(0.0, float(row["minutes"] or 0))
        except (TypeError, ValueError):
            continue
        span = (ended - began).total_seconds()
        if span <= 0:
            day = began.strftime("%Y-%m-%d")
            out[day] = out.get(day, 0.0) + minutes
            continue
        at = began
        while at < ended:                                # its minutes by the share of its span on each day
            nxt = min(ended, (at + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
            day = at.strftime("%Y-%m-%d")
            out[day] = out.get(day, 0.0) + minutes * (nxt - at).total_seconds() / span
            at = nxt
    return {d: round(out[d], 1) for d in sorted(out)}


def _states_days(db: Any, members: List[str]) -> List[str]:
    """The days (the user's own) a task's states were in - each day it was worked on."""
    return sorted(_states_day_minutes(db, members))


#: The most a state that said no minutes of its own counts, in minutes.
SPAN_ONLY_MOST = 180


def _states_minutes(db: Any, members: List[str]) -> float:
    """How long a task's states took, in minutes: each state's own minutes (its metadata), no more than its span - its
    span when it said none (heartbeat/states/task_projects.py worked_minutes) - and never more than the time they
    covered together (2026-11-18: states that overlapped were each counted whole, a six-day task 269 hours)."""
    from datetime import datetime
    ids = [str(one) for one in members if one][:400]
    if not ids:
        return 0.0
    try:
        rows = db.query(f"SELECT metadata, starts_at, ends_at FROM states WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
    except Exception:
        return 0.0
    total, spans = 0.0, []
    for row in rows:
        try:
            said = max(0.0, float((json.loads(row["metadata"] or "{}") or {}).get("minutes") or 0.0))
        except (TypeError, ValueError):
            said = 0.0
        try:
            began = datetime.fromisoformat(str(row["starts_at"]).replace("Z", "+00:00"))
            ended = datetime.fromisoformat(str(row["ends_at"]).replace("Z", "+00:00"))
            span = max(0.0, (ended - began).total_seconds() / 60)
            # A state that said no minutes counts its span - but never more than SPAN_ONLY_MOST (one left open from
            # 09-18 to 09-30 counted eleven days).
            said = min(said, span) if said else min(span, SPAN_ONLY_MOST)
            spans.append((began.timestamp() / 60, began.timestamp() / 60 + said))
        except (TypeError, ValueError):
            pass
        total += said
    covered, end = 0.0, None
    for a, b in sorted(spans):
        if end is None or a > end:
            covered += b - a
            end = b
        elif b > end:
            covered += b - end
            end = b
    if spans:
        total = min(total, covered)
    return round(total, 1)


def important_tasks(db: Any, since: str, until: str) -> List[Dict[str, Any]]:
    """THE IMPORTANT TASKS OF THESE DAYS, each with its materials (the user, 2026-11-17: "每个重要的task都有单独的房间/
    房子/景色之类，里面最好有task相关的item,可以从task的素材里找"): the tasks alive then, by the importance the agent
    gave them, else by the time they took; for each, what its states were seen with - files, pages, messages by their
    titles, and pictures (their kept copies, to frame). Never the screen's pictures."""
    try:
        rows = list(db.query("SELECT id, members, described, created_at, updated_at FROM state_tasks WHERE updated_at >= ? AND created_at < ? "
                             "AND (retired_at IS NULL OR retired_at >= ?)", (since, until, since)))
    except Exception:
        return []
    scored = []
    for row in rows:
        try:
            said = json.loads(row["described"] or "{}")
            members = json.loads(row["members"] or "[]")
        except ValueError:
            continue
        title = " ".join(str(said.get("title") or "").split())
        if not title:
            continue
        importance = said.get("importance")
        importance = int(importance) if str(importance or "").isdigit() else 0
        minutes = float(said.get("minutes") or 0)
        if "minutes" not in said:
            # A TASK FROM BEFORE ITS TIME WAS COUNTED (tasks made before 2026-10-19 have none - the project round began
            # counting then): its time from its states, each its own minutes held to its span, as that round counts.
            minutes = _states_minutes(db, members)
        # EVERY TASK THAT TOOK AN HOUR, NO COUNT FIXED (the user, 2026-11-17: "你理论上不要用固定的重要任务个数，只要比如总activity
        # 超过1小时就算之类"), work and leisure alike ("包括lesisure task 也要作为一个空间").
        if minutes < SMALL_MINUTES:
            continue
        scored.append((importance * 1000 + minutes, row["id"], title, said, members,
                       [str(row["created_at"] or "")[:10], str(row["updated_at"] or "")[:10]]))
    scored.sort(key=lambda one: -one[0])
    chosen = scored
    measured = project_measures(db)
    out = []
    for _score, task_id, title, said, members, lived in chosen:
        materials, pictures, sources = [], [], []
        ids = [str(one) for one in members][-200:]
        if ids:
            marks = ",".join("?" * len(ids))
            try:
                found = list(db.query(f"SELECT kind, title, copies, location FROM state_materials WHERE state_id IN ({marks}) "
                                      f"AND kind IN ('file', 'page', 'message', 'picture') ORDER BY created_at DESC LIMIT 80",
                                      tuple(ids)))
            except Exception:
                found = []
            seen = set()
            for one in found:
                name = " ".join(str(one["title"] or "").split())[:100]
                if one["kind"] == "picture" and len(pictures) < TASK_PICTURES_MOST:
                    try:
                        path = str(json.loads(one["copies"] or "{}").get("picture") or "")
                    except ValueError:
                        path = ""
                    if path and Path(path).is_file():
                        pictures.append({"id": f"task-{task_id}-{len(pictures) + 1}", "path": path,
                                         "what": name or "a picture of this task", "when": ""})
                        continue
                if name and name not in seen and len(materials) < MATERIALS_MOST:
                    seen.add(name)
                    materials.append(f"{one['kind']}: {name}")
                    # WHERE A PICTURE MAY BE FOUND FOR IT (agent/playfind.py): a file they opened, a page they read.
                    where = str(dict(one).get("location") or "").strip()
                    if where and len(sources) < SOURCES_MOST and (
                            (one["kind"] == "page" and re.match(r"(?i)^https?://", where))
                            or (one["kind"] == "file" and Path(where).suffix.lower() in playfind.IMAGE_SUFFIXES + (".pdf",))):
                        sources.append({"id": f"src-{task_id}-{len(sources) + 1}", "kind": one["kind"], "title": name,
                                        "where": where})
        weight = minutes_of(said) + 3 * len(members)
        day_minutes = _project_day_minutes(db, task_id) or _states_day_minutes(db, members)
        # Its measures from its whole history; a task without run tasks (from before they were kept) measured by
        # what it has - its time, its days, its states, what it was seen with.
        own_minutes = minutes_of(said) or _states_minutes(db, members)
        measures = measured.get(task_id) or {"hours": round(own_minutes / 60, 2), "switches": len(day_minutes),
                                             "days": len(day_minutes), "places": 0,
                                             "deliverables": 0.5 * len(materials), "pieces": len(members)}
        complexity = complexity_of(measures)
        out.append({"id": task_id, "title": title[:80], "summary": " ".join(str(said.get("summary") or "").split())[:240],
                    **({"leisure": True} if str(said.get("kind") or "") == "leisure" else {}),
                    **({"small": True} if _score_minutes(said, db, members) < TASK_MINUTES else {}),
                    "lived": lived,                       # when the task was alive: its place is dated by it
                    "day_minutes": day_minutes,           # its ties
                    "measures": measures, "complexity": complexity, "fill": fill_of(complexity),   # its richness
                    "story": project_story(db, task_id, until) if task_id in measured else [],     # how it grew
                    "materials": materials, "pictures": pictures, "sources": sources, "weight": weight,
                    "scale": task_scale(weight),
                    "things": task_things(weight, len(materials) + len(pictures))})
    return out


def minutes_of(said: Dict[str, Any]) -> float:
    try:
        return float(said.get("minutes") or 0)
    except (TypeError, ValueError):
        return 0.0


#: A TASK'S PLACE AS LARGE AS THE TASK (the user, 2026-11-17: "房间的大小，细节度和task耗时，复杂度呈正比"): its weight -
#: the minutes it took and three for each state it holds - to a size, and to how many of its things to add at a time.
TASK_SCALES = ((180, "first"), (600, "full"), (1500, "large"), (float("inf"), "estate"))


#: BOTH LANGUAGES (PlayWorld._translate): how many places' and things' words are asked for at a time; how long each
#: may be.
TASK_TRANSLATE = "play_world_translate"
TRANSLATE_MOST = 60
TRANSLATE_RULE = 2          # 2: only proper nouns kept (2026-11-19) - what was put in under 1 is asked again once
#: Rounds of it in one growth at most - all of the world's words, against a runaway.
TRANSLATE_ROUNDS_MOST = 40
WORD_MOST = {"name": 60, "grounds_name": 60, "why": 500, "room_idea": 300, "summary": 400, "idea": 400, "design": 600,
             "floor_text": 12, "card_title": 80}
TRANSLATE_SYSTEM = """You put the words of a small world that pictures the user's life into @LANGUAGE@ - the language the user has chosen to see it in. Each entry is a place, a thing, a land, a line or a project; its fields are as they were written, in another language. Write each field in @LANGUAGE@ ONLY, EXPLICITLY (the user, 2026-11-19: "生成标牌什么的时候，都要explicitly给language") - never two languages mixed in one, never a sign left as it was - in the same warm, personal voice, as a native writer would say it, not word for word. A "name" and a "grounds_name" go on a sign: at most 24 characters in English, 10 in Chinese, never cut off; a "floor_text" is painted on a floor: at most 12 letters in English, 6 characters in Chinese; a "card_title" names a piece of their work: a few words. ONLY A PROPER NOUN IS KEPT AS IT IS (the user, 2026-11-19: "只有英文专有名词才不翻译") - a brand, a product, an app, a site, a person's or a place's own name (WaterMelon, ChatGPT, Bilibili, StarCatalog); every other word is translated, a project's descriptive name too ("Personalized Agent (WaterMelon)" is "个性化智能体（WaterMelon）", "Reading Club" is "读书会"), and words of another language mixed in are put into @LANGUAGE@ as well. Answer with only JSON: {"<the entry's key>": {"<field>": "<in @LANGUAGE@>", ...}, ...} - every entry, every field."""
#: The languages the world may be put into, as the model is told them.
TRANSLATE_LANGUAGES = {"en": "English", "zh": "Simplified Chinese"}
#: The work trees the world made for tasks without one, kept beside the world (PlayWorld._kept_trees).
TREES_FILE = "trees.json"
#: How many projects' muses are looked for in one growth; how many kinds a room's design is offered.
MUSE_PROJECTS = 4
#: A great ground's plan told this many of each task's cards; never a part of it (a whole land of its own).
GREAT_CARDS_TOLD = 80
GREAT_NOT_PARTS = ("mountain", "volcano", "peak", "sea_tunnel", "estate")
GREAT_GROUND_NOTE = """THIS IS A GREAT GROUND ("great_ground"): one project's whole work as ONE place - its plan given (its idea, \
the site and the human places it draws from, its parts where they lie, the ways through). Design its open ground as one: \
no part fenced from the next, things running across. SHOW THE TREE as its layout; SHOW ITS STAGES ("stages") along \
the way through it, in order - each stage's result standing finished where it is reached (its "result"), the stage \
under way half built, those to come waiting - one thing each saying "stage": <its n>; make ITS FEATURED STEPS \
("featured") the great things of the ground, each saying "card": <its card>."""
#: The world then: this many of its events at a place; shown, where its design chose none, on one of these.
NEWS_A_PLACE, NEWS_KIND = 3, "sign"
ROOM_THING_MOST = (2, 5)    # a room's things up to 2 x 5 tiles, either way round (_room_fit)
#: In a place filled in rows (PlayWorld._fill_rows), a way across every this many tiles.
THICK_ACROSS = 9
#: A TASK'S PLACES AS FULL AS IT IS COMPLEX (the user, 2026-11-18: "每个房间里的东西密度太小了 ... 我现在personal agent这个
#: project, 基本里面空余空间要小于50%"): fill_of(complexity_of(...)) of each of its places' floor holds things - the
#: model's own things there made large (PlayWorld._thicken), never things of the code's choosing.
#: A TASK'S COMPLEXITY, MEASURED MANY WAYS (the user, 2026-11-18: "这个任务的状态数 ÷ 3 + 资料数 这个公式太简单了，比如context
#: switch的数量，deliverable的数量，等等"; "根据我现在personal agent的task complexity, 估计一个ratio (equation), 每个项目的task
#: complexity都按照这个算 ... personal agent的task, 算出来的空地比例要小于一半"). From its whole history (its run tasks):
#: the hours given it, the times the user switched into it from something else, the days it was worked on, the places
#: (folders, sites) it reached, what it delivered (code, documents, data and main files one each; pages and chats a
#: half), and the pieces it was done in - each on a log scale, so none outweighs the rest by being large, weighted:
#:     C = sum(weight * log2(1 + measure))
#: and the share of each of its places that holds things:
#:     fill = FILL_MOST * (1 - exp(-C / FILL_SCALE)), free = 1 - fill.
#: Fitted on 2026-11-18 on real projects: the largest (this app) C 29.8, free 45%; a paper 21.5, 51%; long leisure video
#: 19.8, 54%; a research tool 17.4, 57%; an hour's task about 3, 89%. What it delivers weighs most, so long leisure is
#: not counted as complex work.
COMPLEXITY_WEIGHTS = (("hours", 1.0), ("switches", 0.5), ("days", 0.5), ("places", 0.3), ("deliverables", 1.5),
                      ("pieces", 0.5))
DELIVERABLE_KINDS = {"code": 1.0, "document": 1.0, "data": 1.0, "main": 1.0, "page": 0.5, "chat": 0.5, "other": 0.5}
FILL_MOST, FILL_SCALE = 0.65, 16.0


#: A PROJECT'S STORY, STEP BY STEP (the user, 2026-11-18: "你做的建筑是要对user有纪念意义的，比如一个project, 你要让user 看到他的
#: 成果是如何一步步变好的，比如personal agent一开始的demo是怎么样，后面又是怎么样这种"): a project's whole history in steps of
#: about ten days (the first, middle and last of each month), each what was done then and up to STORY_PICTURES of its
#: own pictures from then - the most colourful, none alike another of the story - shown as it looked at the time.
STORY_PICTURES, STORY_CANDIDATES = 2, 10


def story_step(day: str) -> str:
    """The step of the story a day falls in: "2026-10-1" for the 11th to the 20th of October."""
    d = int(day[8:10] or 1)
    return f"{day[:7]}-{0 if d <= 10 else 1 if d <= 20 else 2}"


def project_story(db: Any, task_id: str, until: str = "") -> List[Dict[str, Any]]:
    """Each step of a project's story up to `until`: its key, its days, what was done (its pieces' titles, the
    most time first), what the largest said of itself, and its pictures from then."""
    try:
        rows = list(db.query("SELECT starts_at, minutes, title, summary, states FROM state_run_tasks "
                             "WHERE project_id = ? AND scope = 'live' ORDER BY starts_at", (task_id,)))
    except Exception:
        return []
    steps: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        day = str(row["starts_at"] or "")[:10]
        if not day or (until and day > until[:10]):
            continue
        one = steps.setdefault(story_step(day), {"from": day, "to": day, "pieces": [], "states": []})
        one["to"] = max(one["to"], day)
        one["pieces"].append((float(row["minutes"] or 0), " ".join(str(row["title"] or "").split()),
                              " ".join(str(row["summary"] or "").split())))
        try:
            one["states"] += [str(s) for s in json.loads(row["states"] or "[]")]
        except ValueError:
            pass
    shown: List[int] = []
    out = []
    for key in sorted(steps):
        one = steps[key]
        pieces = sorted(one["pieces"], key=lambda p: -p[0])
        did = list(dict.fromkeys(title for _m, title, _s in pieces if title))[:4]
        pictures = []
        ids = list(dict.fromkeys(one["states"]))[-400:]
        if ids:
            try:
                found = list(db.query(f"SELECT created_at, title, copies FROM state_materials WHERE kind = 'picture' "
                                      f"AND state_id IN ({','.join('?' * len(ids))}) ORDER BY created_at", tuple(ids)))
            except Exception:
                found = []
            kept = []
            for row in found:
                try:
                    path = str(json.loads(row["copies"] or "{}").get("picture") or "")
                except ValueError:
                    path = ""
                if path and Path(path).is_file():
                    kept.append((str(row["created_at"] or ""), " ".join(str(row["title"] or "").split())[:100], path))
            every = max(1, len(kept) // STORY_CANDIDATES)
            looked = []
            for when, what, path in kept[::every][:STORY_CANDIDATES]:
                made = picture_print(path)
                if made and not any(prints_alike(made[0], s) for s in shown):
                    looked.append((made[1], when, what, path, made[0]))
            for colour, when, what, path, mark in sorted(looked, key=lambda p: -p[0]):
                if len(pictures) >= STORY_PICTURES or any(prints_alike(mark, s) for s in shown):
                    continue
                shown.append(mark)
                pictures.append({"id": f"story-{task_id}-{key}-{len(pictures) + 1}", "path": path,
                                 "what": what or "a picture of the work then", "when": when[:10],
                                 **({"plain": True} if colour < PLAIN_COLOUR else {})})
        out.append({"step": key, "from": one["from"], "to": one["to"], "did": did,
                    "said": (pieces[0][2] if pieces else "")[:240], "minutes": round(sum(p[0] for p in pieces)),
                    "pictures": pictures})
    return out


def complexity_of(measures: Dict[str, Any]) -> float:
    return round(sum(weight * math.log2(1 + max(0.0, float(measures.get(name) or 0)))
                     for name, weight in COMPLEXITY_WEIGHTS), 2)


def fill_of(complexity: float) -> float:
    """The share of a task's places' floor that should hold things, by its complexity."""
    return round(FILL_MOST * (1 - math.exp(-max(0.0, complexity) / FILL_SCALE)), 3)


def project_measures(db: Any) -> Dict[str, Dict[str, float]]:
    """Each project's measures from all its run tasks: hours, switches into it, days, places, deliverables, pieces."""
    try:
        rows = list(db.query("SELECT project_id, minutes, starts_at, places, resources FROM state_run_tasks "
                             "WHERE scope = 'live' ORDER BY starts_at"))
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    last = None
    for row in rows:
        project = str(row["project_id"] or "")
        if not project:
            last = None
            continue
        one = out.setdefault(project, {"minutes": 0.0, "switches": 0, "days": set(), "places": set(),
                                       "made": {}, "pieces": 0})
        one["minutes"] += float(row["minutes"] or 0)
        one["pieces"] += 1
        one["days"].add(str(row["starts_at"] or "")[:10])
        if last != project:
            one["switches"] += 1                        # come to it from something else
        last = project
        try:
            one["places"] |= set((json.loads(row["places"] or "{}") or {}).keys())
        except (ValueError, AttributeError, TypeError):
            pass
        try:
            for res in json.loads(row["resources"] or "[]") or []:
                kind = str(res.get("kind") or "other")
                one["made"][(kind, str(res.get("where") or res.get("what") or ""))] = DELIVERABLE_KINDS.get(kind, 0.5)
        except (ValueError, AttributeError, TypeError):
            pass
    return {p: {"hours": round(o["minutes"] / 60, 2), "switches": o["switches"], "days": len(o["days"]),
                "places": len(o["places"]), "deliverables": round(sum(o["made"].values()), 1), "pieces": o["pieces"]}
            for p, o in out.items()}
#: A thing made large (an arrangement's "scale", and the large things that fill a great work's place): at most this
#: many times its own size every way.
SCALE_MOST = 6
PRINCIPLES = ("contrast", "consistency")
#: Small things that say no more made large - a candle, a crate - never the large things that fill a place.
NOT_LARGE = {"candle", "lamp", "lamp_post", "lantern", "signpost", "chest", "crate", "barrel", "pot", "plant", "stool",
             "chair", "counter", "sink", "hedge", "fireplace", "stove", "beehive", "well"}


def item_size(one: Dict[str, Any]) -> Tuple[int, int]:
    """A thing's tiles across and down: its kind's - times its scale for a picture of the user's alone, whose grain is
    its own; anything else drawn larger would only be coarser (the user, 2026-11-18: "对这种大的东西，一定要细粒度")."""
    if one.get("kind") == "widget" and one.get("widget") in widgets.WIDGETS:     # a progress widget: its own size
        return widgets.size_of(one["widget"])
    if one.get("kind") == "piece":                       # a great piece: its own ground
        return max(1, int(one.get("w") or 1)), max(1, int(one.get("h") or 1))
    sw, sh = ITEM_KINDS.get(one.get("kind") or "", {"size": (1, 1)})["size"]
    scale = max(1, min(SCALE_MOST, int(one.get("scale") or 1))) if one.get("picture") else 1
    return sw * scale, sh * scale


#: NO PICTURE TWICE, NONE ALIKE (the user, 2026-11-18: "我看你放了很多同样的terminal截图，又不美观本质上内容也很同质"): each
#: picture framed is known by a print of its shape (a 64-bit difference hash) and how much colour it has. One whose
#: print is within ALIKE_BITS of a picture already shown is not shown again; one with less colour than PLAIN_COLOUR (a
#: terminal, a blank page) is shown once in a place at most.
ALIKE_BITS = 10
PLAIN_COLOUR = 16.0


def picture_print(path: Any) -> Optional[Tuple[int, float]]:
    """A picture's print and its colourfulness (Hasler and Suesstrunk's measure); None when it cannot be read."""
    import math
    try:
        from PIL import Image
        with Image.open(path) as image:
            grey = list(image.convert("L").resize((9, 8)).getdata())
            small = list(image.convert("RGB").resize((48, 48)).getdata())
    except Exception:
        return None
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | int(grey[y * 9 + x] > grey[y * 9 + x + 1])
    rg = [r - g for r, g, _b in small]
    yb = [(r + g) / 2 - b for r, g, b in small]

    def spread(values: List[float]) -> Tuple[float, float]:
        mean = sum(values) / len(values)
        return mean, math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    (mean_rg, sd_rg), (mean_yb, sd_yb) = spread(rg), spread(yb)
    return bits, math.hypot(sd_rg, sd_yb) + 0.3 * math.hypot(mean_rg, mean_yb)


def prints_alike(a: int, b: int) -> bool:
    return bin(a ^ b).count("1") <= ALIKE_BITS


def task_scale(weight: float) -> str:
    return next(name for limit, name in TASK_SCALES if weight < limit)


def task_things(weight: float, materials: int) -> int:
    return int(max(3, min(MOST_NEW_ITEMS, 3 + weight / 120 + materials / 2)))


def drawn_pixels(said: Any, most_w: int, most_h: int) -> Optional[Dict[str, Any]]:
    """A thing the agent drew, checked: up to ten colours, rows of digits (a colour each) or "." (none), no larger than
    its kind's. None when it is not a picture."""
    if not isinstance(said, dict):
        return None
    palette = [c for c in (said.get("palette") or []) if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c)][:10]
    rows = [re.sub(r"[^0-9.]", ".", str(r))[:most_w] for r in (said.get("rows") or [])][:most_h]
    if not palette or sum(1 for r in rows for ch in r if ch != ".") < 6:
        return None
    return {"palette": palette, "rows": rows}


def _task_line(described: Any) -> str:
    try:
        said = json.loads(described or "{}")
    except ValueError:
        return ""
    return " - ".join(str(said.get(key) or "") for key in ("title", "summary", "goal", "doing") if said.get(key))


def earliest_lived(db: Any) -> str:
    """When the first of what is kept of the user's days was."""
    found = []
    for sql in ("SELECT MIN(created_at) AS at FROM messages WHERE role = 'user'",
                "SELECT MIN(created_at) AS at FROM states",
                "SELECT MIN(first_at) AS at FROM reading_pieces", "SELECT MIN(created_at) AS at FROM todo_items",
                "SELECT MIN(created_at) AS at FROM state_projects"):
        try:
            row = db.query_one(sql)
            if row and row["at"]:
                found.append(str(row["at"]))
        except Exception:
            continue
    return min(found) if found else ""


def chat_pictures_offered(conversations: Any, conversation_id: str, folder: Path,
                          since: str = "", most: int = MOST_OFFERED) -> List[Dict[str, Any]]:
    """The pictures that went with the user's messages in the side panel, newest first - each its id, when, what is in
    it (its summary) and its file - for the world's frames."""
    out: List[Dict[str, Any]] = []
    try:
        messages = conversations.messages(conversation_id, limit=None)
    except Exception:
        return out
    for one in reversed(messages or []):
        picture = ((one.get("metadata") or {}).get("picture") or {})
        path = Path(folder) / str(picture.get("file") or "")
        when = str(one.get("created_at") or "")
        if not picture.get("file") or not path.is_file() or (since and when and when < since):
            continue
        out.append({"id": f"pic-{one.get('id')}", "when": when[:16], "path": str(path),
                    "what": str(picture.get("summary") or one.get("content") or "")[:400]})
        if len(out) >= most:
            break
    return out


__all__ = ["task_lineage", "PlayWorld", "ZONE_KINDS", "ITEM_KINDS", "cells", "chat_pictures_offered", "lived_between", "earliest_lived",
           "look_up", "TASK_LOOK", "tastes_of", "important_tasks",
           "TASK"]

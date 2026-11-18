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

import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..models.base import ModelRequest
from . import playplaces as places
from . import playregions as regions

log = logging.getLogger(__name__)

from . import playcatalogue as catalogue
TASK = "play_world_grow"
#: The search before it: what the model wants to know, looked up in the agent's own records.
TASK_LOOK = "play_world_look"
#: Things compared by their pictures before one is chosen (agent/playcatalogue.py).
TASK_PICK = "play_world_pick"
#: The themes chosen first, their things given after (agent/playcatalogue.py theme_tree).
TASK_THEMES = "play_world_themes"
THEMES_MOST = 4
THEMES_SYSTEM = """You keep a small world that pictures the user's life, and are about to grow it from these days. \
Its things come in themes, the themes in groups ("THEMES": group -> theme -> what it is). Choose the themes - at most \
{most} - whose things this growth may want: what these days were, the lands the world has, a festival or a season they \
lived, a place's style. You will be given the things of those you choose. Answer with only JSON: \
{{"themes": ["<theme>", ...]}}."""
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
}
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
    "wall_art": {"size": (2, 1), "wall": True, "pixels": (32, 16)},
    # Living things (Tiny Creatures, CC0): a pet, a robot.
    "cat": {"size": (1, 1)}, "dog": {"size": (1, 1)}, "rabbit": {"size": (1, 1)}, "robot": {"size": (1, 1)},
    # Festivals, fairs, the sea, a graveyard, a garden's (2026-11-17).
    "gravestone": {"size": (1, 1)}, "coffin": {"size": (2, 2)}, "pumpkin": {"size": (1, 1)},
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
PICTURE_SIDE = 320
#: How often the day's growth is looked for (and while catching up); and the chat pictures offered, at most.
LOOK_SECONDS, BEHIND_SECONDS, MOST_OFFERED = 1800, 300, 12
#: Each growth draws this many days of what was lived; the earliest drawn is at most this long ago.
STEP_DAYS, BACK_DAYS = 14, 180

SYSTEM = """You keep a small world that is your picture of the user's life - a place they can walk in, made of \
rooms (zones) with things in them. You see what the user lived lately; grow the world from it, mixing what really \
happened with your own imagination of what it means to them.

Rules:
- Only ADD: new zones and new things. Never remove or move what is there (at most {zones} zones and {items} things).
- MAKE IT RICH, AS RICH AS THEIR LIFE (the user: the world should be large and detailed): a fortnight of a varied life \
is usually 3 to 6 new places and 20 to 40 things - furniture, light, rugs, what hangs on walls, plants, the small \
things of each activity. Never a bare room.
- FIRST SAY WHETHER THE CONTENT SWITCHED: "content_switch": {{"switched": true or false, "what": "..."}} - true only \
when these days show a new part of their life the world does not hold yet (a new subject, activity, place, people), \
"what" naming it. NOT SWITCHED: add no zone - make the rooms that are there fuller and finer instead: 3 to 6 things \
that fit what they did, the furniture, the light, the rugs, what hangs on the walls, the small things that make a room \
lived in. SWITCHED: you may add a zone for it (new zones are refused otherwise), and things in it.
- THEIR TASTES ("their_tastes": what the agent has learned they like and dislike): let them choose the styles - a \
region's settings, a place's style, the things and their colours. Their world should look like what they would love.
- EVERY IMPORTANT TASK GROUND OF ITS OWN ("important_tasks") - the more complex the task, the more ground it takes: \
a room, an open place, or both - a house with its yard, garden, pool or park ("grounds": one of the open kinds, \
"grounds_name": its name), whichever fits the task; you judge it. Its main place named as the task is - its title, lightly polished, short, but a project's or a product's own name \
kept exactly as it is (e.g. "StarCatalog") - the zone given "task": "<task id>".
- A SIGN HOLDS LITTLE: every name you give - a place's, its grounds', the ground between neighbours - is written \
on its sign. At most 24 characters (two or three words; in Chinese at most 10 characters): a task's long title made a \
short name, never cut off half way. What it is about goes in "why", which is read on its card.
- A TASK'S PLACE KNOWN AT A GLANCE: make its floor its project's - "decorate": [{{"zone": "<id or new zone's name>", \
"floor_text": "<its name, up to 12 letters, spelled large on the floor>", "floor_color": "#rrggbb", \
"floor_picture": "<a picture id from the task's pictures - its logo, say - laid on the floor>", "mural": {{"palette": \
["#rrggbb", ...up to 8], "rows": ["<up to 24 characters, each a digit 0-7 for a palette colour or . for none>", ...up \
to 16 rows]}} (a picture you draw yourself, laid on the floor), "name": "<a new name, only to put back a task's own \
name it lacks>"}}] - any of them, for a new place or one already there. Every room of a project should be unmistakably \
that project's. A task's grounds hold its things as its rooms do.
- CUSTOMIZED THINGS, the project's own (the user: "有customized东西"): a "statue_of", a "banner" or a "poster" holding \
one of its pictures ("picture": its id - a logo, a screenshot); or a thing you draw yourself - "pixel_art" (16x16), \
"pixel_art_large" (32x32), "wall_art" (32x16, on a wall) with "pixels": {{"palette": ["#rrggbb", ...up to 10], \
"rows": ["<a digit per pixel for its colour, . for none>", ...]}} - a robot for a robot, a rocket for a launch, the \
thing itself. A project's place has a few of them. Its \
size follows how long the task took and how complex it was (set by the world); add about "things_to_add" of its \
things this time, every one about the task. Other places, not a task's, are for the rest of their life. One without a place ("its_place" empty) gets one now - that is new \
content enough. Its things come from the task's materials: a file or a document as a bookshelf, a desk or a cabinet \
holding it, a page as a map or a painting, a message as a wall shelf or a lamp by the desk, a picture in a "frame" \
(its id from the task's "pictures") - each thing's "from" naming the material. A task with a place: add to its place.
- NATURE TOO (the user: "你也要加自然景观，喷泉，小岛，瀑布之类的"): between the buildings, natural places - a lake with an \
islet, a waterfall, a fountain plaza, a grove, a meadow - where the land and their life would have them (a quiet \
lake for a calm stretch, a waterfall for a rush of work...). They are zones like any other, named.
- THE GROUND BETWEEN NEIGHBOURS ("neighbourhoods": projects whose places stand close, with nothing between them yet): \
think of them together - what they share, what they make when put side by side - and abstract it into a new idea, a \
word or two of your own; then make a place between them named for that idea and furnished from it ("between": \
[their zone ids], "kind": an open kind or a building, "name": the idea, "why": how the idea came from them). One for \
each neighbourhood, when you see one worth making.
- GATHERED AS THEIR LIFE GATHERS (the user: "任务之后可以合并，规划成一个小镇或者大house"): places of tasks that belong \
together may be merged - "merge": [{{"into": "town" or "house", "name": "...", "zones": [two or more ids], "why": "...", \
"style": "<a building style, for a house>"}}]. A TOWN: they move together round a square of that name. A GREAT HOUSE: \
they become its rooms, every thing in them carried in, each room keeping its name and days. Rarely, and only when \
what they share has grown.
- SMALL TASKS, ONE WHOLE SCENE (the user: "对于小任务 ... 进行grouping进行完整景观建造"): "small_tasks" are too small for a \
place each. Group those alike - in what they are about, and when - into one place, a whole scene: a park whose corners \
are theirs, a market street of their stalls, a house of their rooms ("zones": [{{"kind", "name": the group's theme, \
"why", "tasks": [their ids]}}]); in it a thing for each ("items": [{{..., "task": "<its id>"}}]). A small task with \
"its_place" has one: add to it, do not make another.
- LIKE WITH LIKE: lay a task's place near the place most like it - in what it is about and when it was - its \
"near": that place's id; the road of their life puts it after what came before it in time, and your "near" keeps like \
things together. A task UNLIKE EVERY OTHER may stand apart: "apart": "island" (an island in the sea) or "cave" (a cave \
alone in the dark) - its own land, no road to it, reached only by a portal. Seldom; only what truly stands alone.
- LEISURE IS A PLACE TOO (an important task with "leisure": true): what they do for joy - a game, a show, a hobby - gets \
its own place as work does: an arcade, a cinema, a garden, a court, whatever the joy is.
- A SIGN'S LOOK: any new or redesigned place may say "sign": one of {signs} - else it suits the place's style.
- THREE WAYS TO THINK OF A NEW PLACE: of your own, from what they lived; COMBINED - the ground between neighbours, \
above; or ON THE WAY IT GOES ("lines": a place A, then B after it, the road going on from B): the change from A to B \
carried one step further - what B becomes if it goes on as it came (a cottage, then a manor, then a palace; a garden, \
then a greenhouse, then a botanic garden; a study of bees, then a hive, then a meadow humming with them). Make that next \
place, C, its theme your own from that direction ("goes_on": [A's id, B's id], "kind", "name": the idea, "style" if a \
building, "why": how A to B led here). It stands on the line beyond B. One for a line, when you see one worth it.
- PLACES GROW AS THEIR LIFE DEEPENS. A zone is a building or an open place: buildings {indoor_kinds}; open places \
{outdoor_kinds} (a pool has water in its middle; a park, a courtyard, a terrace are open to walk). Make them as rich as \
what they did is - small and plain for a small thing, grand for what fills their life: \
A new zone may say "near": "<zone id>" - the place it belongs with, to stand by it (places gather in clusters, not \
rows). "enlarge": [{{"zone": "<id>", "why": "..."}}] makes a place larger (to its fullest, then a wing more, then an \
estate - a mansion of rooms); "redesign": [{{"zone": "<id>", "style": one of {styles}, "scale": one of {scales}, "why": \
"..."}}] rebuilds it in a new style and size - a small room may become a mansion. Every thing in it is kept, only laid \
out again. A place marked "full" takes nothing more until it is enlarged or redesigned. At most {enlarged} enlarged \
and {redesigned} redesigned at a time.
- Zone kinds (choose only these): {zone_kinds}. Thing kinds (choose only these): {item_kinds}. "frame" hangs one of \
the user's pictures (give its "picture" id from PICTURES); the others are furniture and plants that say something \
of them. "tree", "pine", "bush" and "flowerpot" suit the garden.
- Every thing has "why": in the first person, one or two short sentences of why you made it and what you feel or \
think of the user in it - warm, honest, specific to what they did, never flattery. And "from": the moment of \
their life it comes from (what, and when).
- A thing goes in an existing zone by its id, or in a zone you add now by that zone's name.
- Names are short (a few words). Nothing that would embarrass them if a friend walked through.
- REGIONS: the world is made of regions, each with a style of its own. Everyone begins in the Starting Village, the \
same for all. When "a_new_region" is given, you may open a new region - "region": {{"name", "why", "biome", "layout", \
"palette", "architecture", "theme", "landmark", "weather", "lights", "music"}} - each setting one of its list: biome \
{biomes}; layout {layouts}; palette {palettes}; architecture {architectures}; theme {themes}; landmark {landmarks}; \
lights {lights} (its street lights - a gas lamp for a village, a torch for a wild land...; optional); weather \
{weathers} (changing: by the land's chances; or always one; or an eternal night, winter, dusk or spring - when \
that is what this part of their life feels like). \
"leave": you now know them well enough to lead them out of the Starting Village - choose, from the whole of their \
life, a land that is unmistakably theirs (a combination that fits them, not the first of each list). "change": open \
one only if what they do has changed greatly - a new chapter of their life; otherwise leave "region" out. "full": the \
region they are in has no room left - open one, its style the same or moved on as their life has. Its "why" says, in \
the first person, why this land is them. New zones go in the newest region.
- A new zone (and a new region) has its own little tune, which you write - its instrument and arrangement fitting what the place is to them (a library a piano, a study strings, a garden a flute over a harp, a cafe a guitar waltz, a cave an organ...; different places sound different). "music": {{"track": one of {tracks} - a recorded piece, ONLY where one truly fits this place (see which places already have which: do not give many places the same one), else leave it out and write the tune: "tempo": 50-160, "instrument": one of {instruments}, "accompaniment": one of {instruments}, "style": one of {arrangements}, "mode": major or minor, "melody": 8-32 eighth notes as note names with octave (C5, F#4, Bb5) or "-" for a rest, space-separated, "bass": 4-8 notes, one a bar, the chords built on them}}. Every other sound comes only from a thing that makes it: a "fireplace" or a "stove" crackles - add one if the place should have that sound; nothing else is heard.

Answer with only JSON:
{{"content_switch": {{"switched": false, "what": ""}},
 "enlarge": [...], "redesign": [...], "decorate": [...],
 "region": {{...}} (only when you open one),
 "zones": [{{"kind": "...", "name": "...", "why": "...", "music": {{...}}}}],
 "items": [{{"zone": "<zone id or a new zone's name>", "kind": "...", "name": "...", "why": "...", "from": "...", \
"picture": "<a PICTURES id, or empty>"}}]}}
Empty lists when nothing new is worth adding.
- THE THINGS THERE ARE, BY THEME ("THINGS_BY_THEME": the themes chosen for this growth and the lands', each with what \
it is and its things as "id: what it is (size)"): \
any thing's id is a "kind" you may place, besides the kinds above - a mausoleum in a cemetery, a ferris wheel at a fair, \
lanterns at the New Year, a fish tank in an aquarium. Choose by their words; unsure which looks right, give the item \
"compare": [two to four ids] in place of "kind" - you will be shown them side by side to choose."""


#: A ROOM'S SOUND (the user, 2026-11-17: "不同场景可以是不同音效（这个音效可以ai 生成吗，不能的话ai根据场景自己选吧)"): the
#: agent writes each new room's tune as notes - played by the page's own synthesis (api/web/play-sound.js) - and
#: chooses the sound around it from these. A room without either plays its kind's own.
AMBIENCES = ("none", "birds", "rain", "wind", "crowd", "fire", "water")
INSTRUMENTS = ("piano", "strings", "flute", "harp", "guitar", "musicbox", "marimba", "organ", "celesta", "pad")
#: Recorded tracks a place may have, by their mood (api/web/play/music, CC0).
TRACKS = {"home": "warm, homely", "friends": "light, friendly, sociable", "peaceful": "calm water, still",
          "sand": "sunny, playful, by the sea", "summer": "bright, outdoors, carefree", "innocence": "tender, thoughtful, night",
          "town": "bustling, cheerful town", "piano": "solo piano, studious", "piano-calm": "quiet piano, deep focus"}
#: How a tune is arranged under its melody (api/web/play-sound.js).
ARRANGEMENTS = ("solo", "arpeggio", "pad", "waltz", "march")
_NOTE = re.compile(r"^[A-G][#b]?[1-7]$")


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

    def __init__(self, folder: Path, models: Any, config: Any, context: Optional[Callable[[], str]] = None,
                 pictures: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 history: Optional[Callable[[str, str], str]] = None,
                 earliest: Optional[Callable[[], str]] = None,
                 look: Optional[Callable[[List[str], str], str]] = None,
                 tastes: Optional[Callable[[], str]] = None,
                 tasks: Optional[Callable[[str, str], List[Dict[str, Any]]]] = None) -> None:
        self.folder = Path(folder)
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
            return world

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
            world["placed"] = 2
            home = self._add_zone(world, "home", "Home", "Where you start and end each day.", now)
            for kind in ("bed", "window", "lamp"):
                self._add_item(world, home, {"kind": kind, "name": "", "why": "", "from": ""}, now)
            self.save(world)
            return world

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

    def window(self, world: Dict[str, Any], now: str) -> Tuple[str, str]:
        """The days the next growth draws: from where it was caught up to, STEP_DAYS on - never past now."""
        from datetime import timedelta
        since = str(world.get("caught_up") or world.get("born") or now)
        try:
            until = min(datetime.fromisoformat(since) + timedelta(days=STEP_DAYS), datetime.fromisoformat(now))
        except ValueError:
            return since, now
        return since, until.isoformat(timespec="seconds")

    def behind(self, world: Optional[Dict[str, Any]] = None, now: Optional[str] = None) -> bool:
        """Whether more than a step of what was lived is not drawn yet."""
        from datetime import timedelta
        world = world or self.load() or {}
        try:
            return datetime.fromisoformat(str(world.get("caught_up") or "")) + timedelta(days=STEP_DAYS) \
                < datetime.fromisoformat(now or _now())
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
        ident = f"z{len(world['zones']) + 1}"
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
            world["items"].append({"id": f"i{len(world['items']) + 1}", "zone": zone["id"], "kind": "fountain",
                                   "x": zone["x"] + w // 2 - 1, "y": zone["y"] + 1 + h // 2 - 1, "name": name[:60],
                                   "why": "", "from": "", "made": at, "picture": ""})
        if kind == "grove":                           # its trees, scattered through it, a step apart
            fx, fy = zone["x"], zone["y"] + 1
            taken = set()
            for n in range(14):
                tx, ty = fx + 1 + (n * 5) % max(1, w - 2), fy + (n * 3 + n // 3) % max(1, h - 1)
                if any(abs(tx - a) < 2 and abs(ty - b) < 2 for a, b in taken):
                    continue
                taken.add((tx, ty))
                world["items"].append({"id": f"i{len(world['items']) + 1}", "zone": zone["id"],
                                       "kind": ("tree", "pine", "tree", "bush")[n % 4], "x": tx, "y": ty, "name": "",
                                       "why": "", "from": "", "made": at, "picture": ""})
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
            w, h = ITEM_KINDS.get(one["kind"], {"size": (1, 1)})["size"]
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
        spot = self._place(world, zone, kind)
        while spot is None and self._grow_room(world, zone, at, f"room for {said.get('name') or kind}"):
            spot = self._place(world, zone, kind)
        if spot is None:
            # FULL AT ITS LARGEST: nothing more here - no room beside it made by itself (a new place is for new
            # content only); the model is told it is full, to enlarge it or redesign it.
            return None
        item = {"id": f"i{len(world['items']) + 1}", "zone": zone["id"], "kind": kind,
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
                offered = list(self.pictures() or [])[:MOST_OFFERED]
            except Exception:
                log.debug("the user's pictures could not be offered to the world", exc_info=True)
        here = {"zones": [{"id": one["id"], "kind": one["kind"], "name": one["name"], "made": one["made"][:10],
                           "style": one.get("style", ""), "scale": "x".join(map(str, places.span_of(one))),
                           "music": (one.get("music") or {}).get("track") or ("its own tune" if one.get("music") else ""),
                           "size": f"{one['w']} x {one['h']}", **({"full": True} if self._full(world, one) else {})}
                          for one in world["zones"]],
                "items": [{"zone": one["zone"], "kind": one["kind"], "name": one["name"], "made": one["made"][:10]}
                          for one in world["items"]][-80:]}
        current = world["regions"][-1]
        here["region_now"] = {key: current.get(key) for key in ("name", "starter", *regions.SETTINGS)}
        opening = regions.may_leave(world, self.drawn_days(world))
        looked = self._looked(since, until, lived, about)
        chosen = self._themes(since, until, lived, world)
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
        for one in important:
            offered.extend(one.get("pictures") or [])
        # Small tasks apart: grouped by the model into whole scenes (none of them a place of its own).
        small = [one for one in important if one.get("small")]
        important = [one for one in important if not one.get("small")]
        self._small_ids = {str(one["id"]) for one in small if str(one["id"]) not in placed}
        self._small_titles = {str(one["id"]): one["title"] for one in small}
        self._small_minutes = {str(one["id"]): float(one.get("weight") or 0) for one in small}
        self._important = {one["id"] for one in important if one["id"] not in placed}
        self._important_info = {one["id"]: one for one in important}
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
                                        "pictures": [p["id"] for p in one.get("pictures") or []]}
                                       for one in important]} if important else {}),
               "who_they_are_now": about[:4000],
               "THINGS_BY_THEME": catalogue.by_theme(chosen),
               "PICTURES": [{"id": one["id"], "when": one.get("when", ""), "what": one.get("what", "")[:300]}
                            for one in offered]}
        system = SYSTEM.format(zones=MOST_NEW_ZONES, items=MOST_NEW_ITEMS, zone_kinds=", ".join(ZONE_KINDS),
                               biomes=", ".join(regions.BIOMES), layouts=", ".join(regions.LAYOUTS),
                               palettes=", ".join(regions.PALETTES), themes=", ".join(regions.THEMES),
                               architectures=", ".join(regions.ARCHITECTURES),
                               landmarks=", ".join(regions.LANDMARKS), weathers=", ".join(regions.WEATHERS),
                               lights=", ".join(regions.LIGHTS),
                               instruments=", ".join(INSTRUMENTS), arrangements=", ".join(ARRANGEMENTS),
                               tracks="; ".join(f"{k} ({v})" for k, v in TRACKS.items()),
                               indoor_kinds=", ".join(k for k in ZONE_KINDS if k not in places.OUTDOOR),
                               outdoor_kinds=", ".join(places.OUTDOOR), styles=", ".join(places.STYLES),
                               scales=", ".join(places.SCALES), enlarged=MOST_ENLARGED, redesigned=MOST_REDESIGNED,
                               item_kinds=", ".join(k for k in ITEM_KINDS if not ITEM_KINDS[k].get("thing")),
                               signs=", ".join(SIGN_STYLES))
        try:
            response = self.models.invoke(ModelRequest(
                system=system, task=TASK, max_tokens=8000, builtin_web=False,
                messages=[{"role": "user", "content": json.dumps(ask, ensure_ascii=False)}],
                language=in_display('"name", "why" and "from"')), role="primary")
            text, _cut = read_text(response)
            match = re.search(r"\{.*\}", text or "", re.DOTALL)
            said = json.loads(match.group(0)) if match else {}
        except Exception as exc:
            log.info("the world could not grow", exc_info=True)
            return {"error": str(exc)[:200]}
        self._pick(said)
        return self.apply(said, offered, at, (since, until))

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
                     "lands_the_world_has": sorted(set(lands)), "THEMES": tree}, ensure_ascii=False)}],
                language=in_display("nothing")), role="primary")
            text, _cut = read_text(response)
            match = re.search(r"\{.*\}", text or "", re.DOTALL)
            said = (json.loads(match.group(0)) if match else {}).get("themes") or []
            chosen = [str(one) for one in said if str(one) in known][:THEMES_MOST]
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
                messages=[{"role": "user", "content": content}], language=in_display("nothing")), role="primary")
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
                language=in_display("the searches")), role="primary")
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
            by_name: Dict[str, Dict[str, Any]] = {}
            zones_added = items_added = 0
            switch = said.get("content_switch") if isinstance(said.get("content_switch"), dict) else {}
            switched = switch.get("switched") is True
            # A new region for a change, or a new zone, only when the content switched; leaving the village, or a
            # full region, needs no switch.
            opened = (self._open_region(world, said.get("region"), at, days)
                      if switched or regions.may_leave(world, self.drawn_days(world)) in ("leave", "full") else None)
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
                if not (switched or for_task or neighbours or going or grouped):
                    continue
                kind = str(one.get("kind") or "")
                name = " ".join(str(one.get("name") or "").split())[:60]
                if kind not in ZONE_KINDS or not name:
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
                if (kind == "frame" or ITEM_KINDS[kind].get("picture")) and picture_id not in pictures:
                    continue                           # it holds one of the user's pictures, or is not made
                pixels = None
                if ITEM_KINDS[kind].get("pixels"):
                    pixels = drawn_pixels(one.get("pixels"), *ITEM_KINDS[kind]["pixels"])
                    if pixels is None:
                        continue                       # drawn by the agent, or not made
                item = self._add_item(world, zone, one, at)
                if item is None:
                    continue
                if days:
                    item["days"] = [days[0][:10], days[1][:10]]       # the days it comes from
                if picture_id in pictures:
                    item["picture"] = self._copy_picture(pictures[picture_id], item["id"])
                if pixels:
                    item["pixels"] = pixels
                items_added += 1
            self._decorate(world, said.get("decorate"), offered, by_name, at)
            self._fill_task_places(world, at, days)
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
        fits = r[0] >= b[0] and r[1] >= b[1] and r[2] <= b[2] and r[3] <= b[3] and not any(
            r[0] < g[2] and r[2] > g[0] and r[1] < g[3] and r[3] > g[1] for g in grown)
        if fits:
            spot = (zone["x"], zone["y"])
        elif here:
            spot = here
        else:
            return False
        zone["x"], zone["y"], zone["w"], zone["h"] = spot[0], spot[1], w, h
        if span:
            zone["span"] = list(span)
        zone["door"] = zone["x"] + 1 + zone["w"] // 2
        return True

    def _move(self, world: Dict[str, Any], zone: Dict[str, Any], towards: Tuple[float, float]) -> bool:
        """A place moved, as it is, to the room nearest `towards`; its things with it. Whether it could be."""
        region = next((one for one in world["regions"] if one["id"] == zone.get("region")), world["regions"][0])
        others = [places.zone_rect(one) for one in world["zones"] if one["id"] != zone["id"]]
        spot = places.find_spot(world, region, zone["kind"], zone["w"], zone["h"], towards, zone["id"], others)
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

    def _relayout(self, world: Dict[str, Any], zone: Dict[str, Any]) -> None:
        """EVERY THING IN A PLACE LAID OUT AGAIN in its new shape - in the order they were made; none ever dropped (one
        that finds no spot keeps where it was)."""
        mine = [one for one in world["items"] if one["zone"] == zone["id"]]
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
                item = self._add_item(world, zone, {"kind": kind, "name": title[:40], "why": "", "from": material}, at)
                if item is None:
                    break
                if days:
                    item["days"] = [days[0][:10], days[1][:10]]
                want -= 1
            for pid, picture in pictures.items():
                if want <= 0 or pid in used:
                    continue
                item = self._add_item(world, zone, {"kind": "frame", "name": picture.get("what", "")[:40], "why": "",
                                                    "from": f"picture: {picture.get('what', '')}"}, at)
                if item is None:
                    break
                item["picture"] = self._copy_picture(picture, item["id"])
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
        if not isinstance(said, dict) or not regions.may_leave(world, self.drawn_days(world)):
            return None
        style = regions.chosen(said)
        name = " ".join(str(said.get("name") or "").split())[:60]
        if style is None or not name:
            return None
        region = regions.open_region(world, style, name, str(said.get("why") or ""), at, days, CELL_W, CELL_H)
        music = tune_of(said.get("music"))
        if music:
            region["music"] = music
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
                image.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
                image.save(target, "JPEG", quality=82)
        except Exception:
            try:
                shutil.copyfile(source, target)
            except OSError:
                return ""
        return target.name

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
        while not self._stop.wait(BEHIND_SECONDS if self.behind() else LOOK_SECONDS):
            try:
                if self.due():
                    said = self.grow()
                    log.info("the play world grew: %s", said)
            except Exception:
                log.info("the play world's growth failed", exc_info=True)

    def grow_soon(self) -> bool:
        """Grown now, off the caller's thread (the world opened the first time, or the user asked). Whether begun."""
        if self._growing.locked():
            return False
        threading.Thread(target=lambda: log.info("the play world grew: %s", self.grow()), name="play-world-grow",
                         daemon=True).start()
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
#: A task takes this many minutes of their activity in the days drawn to be given a place; no count fixed. One of less
#: but at least SMALL_MINUTES is a small task: grouped with others like it into one whole scene.
TASK_MINUTES, SMALL_MINUTES = 60, 20
#: How a place's sign may look (api/web/play.js PLAY_SIGN_STYLES).
SIGN_STYLES = ("wood", "stone", "metal", "scroll", "neon", "gold", "red", "pink")


def _score_minutes(said: Dict[str, Any], db: Any, members: List[str]) -> float:
    return float(said.get("minutes") or 0) if "minutes" in said else _states_minutes(db, members)


def _states_minutes(db: Any, members: List[str]) -> float:
    """How long a task's states took, in minutes: each state's own minutes (its metadata), no more than its span - its
    span when it said none (heartbeat/states/task_projects.py worked_minutes)."""
    from datetime import datetime
    ids = [str(one) for one in members if one][:400]
    if not ids:
        return 0.0
    try:
        rows = db.query(f"SELECT metadata, starts_at, ends_at FROM states WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
    except Exception:
        return 0.0
    total = 0.0
    for row in rows:
        try:
            said = max(0.0, float((json.loads(row["metadata"] or "{}") or {}).get("minutes") or 0.0))
        except (TypeError, ValueError):
            said = 0.0
        try:
            began = datetime.fromisoformat(str(row["starts_at"]).replace("Z", "+00:00"))
            ended = datetime.fromisoformat(str(row["ends_at"]).replace("Z", "+00:00"))
            span = max(0.0, (ended - began).total_seconds() / 60)
            said = min(said, span) if said else span
        except (TypeError, ValueError):
            pass
        total += said
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
    out = []
    for _score, task_id, title, said, members, lived in chosen:
        materials, pictures = [], []
        ids = [str(one) for one in members][-200:]
        if ids:
            marks = ",".join("?" * len(ids))
            try:
                found = list(db.query(f"SELECT kind, title, copies FROM state_materials WHERE state_id IN ({marks}) "
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
        weight = minutes_of(said) + 3 * len(members)
        out.append({"id": task_id, "title": title[:80], "summary": " ".join(str(said.get("summary") or "").split())[:240],
                    **({"leisure": True} if str(said.get("kind") or "") == "leisure" else {}),
                    **({"small": True} if _score_minutes(said, db, members) < TASK_MINUTES else {}),
                    "lived": lived,                       # when the task was alive: its place is dated by it
                    "materials": materials, "pictures": pictures, "weight": weight, "scale": task_scale(weight),
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


__all__ = ["PlayWorld", "ZONE_KINDS", "ITEM_KINDS", "cells", "chat_pictures_offered", "lived_between", "earliest_lived",
           "look_up", "TASK_LOOK", "tastes_of", "important_tasks",
           "TASK"]

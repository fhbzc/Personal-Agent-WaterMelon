"""THE WORLD'S FEELING, ITS MUSES AND ITS ROOMS (the user, 2026-11-18: "我感觉你现在还是创意不够").

How a growth is conceived now, in the user's order:

1. THE FEELING OF THESE DAYS CHOOSES THE SCENE ("我希望整体风格可以变化，比如现在是小岛，之后又变成别的什么，这个能根据我
   在不同时间段的情绪感知做吗（比如我某段时间悲伤，就是雪天？）"): read from their own words in their chats and the
   rhythm of their work, and nothing else; a land of each stretch keeps the look its feeling gave it. A scene comes with
   its weather ("场景要和天气搭配，比如雪山就是雪天"), held by the code, not left to the model.
2. MUSES, NOT RULES ("让agent用诗歌，音乐，绘画，视频各找一个符合她意境的内容（可以自己写，可以找），然后用这些素材做设计，
   最后拼凑。不要条条框框"): for each project a poem, a piece of music, a painting and a video, found or made; the four
   are put in the world ("可以放进去") - what is found shown only when it is the public's to show, else its link.
3. EACH IMPORTANT ROOM DESIGNED ON ITS OWN, tile by tile, at its things' own sizes and filled by layers - the wall,
   what hangs, what stands on a table, the floor, what lies under foot ("室内的东西的size就正常（比如床6格），你只能靠加
   布置") - then drawn as a picture, looked at once and laid again with what was wrong put right.
4. THE RULES ARE THE CODE'S ("条条框框修饰有的是代码level的保证"): sizes, the door's way, every thing reached, nothing on
   a wall but what hangs there - checked here and in playworld, never asked of the model.
"""

from __future__ import annotations

import base64
import io
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .playshapes import OUTLINES

WEB = Path(__file__).resolve().parents[1] / "api" / "web"
PLAY = WEB / "play"

TASK_MOOD = "play_world_mood"
TASK_MUSE = "play_world_muse"
TASK_ROOM = "play_world_room"
TASK_ROOM_LOOK = "play_world_room_look"
TASK_TREE = "play_world_tree"

# -- 1. the feeling of these days ------------------------------------------------------------------------------------
#: EACH SCENE WITH ITS WEATHER AND ITS LIGHT, kept together by the code: a snowy mountain snows.
MOOD_SCENES: Dict[str, Dict[str, str]] = {
    # light, hopeful, at ease
    "spring_meadow": {"biome": "meadow", "weather": "eternal_spring", "palette": "spring", "layout": "village",
                      "what": "a meadow village in spring - light, hopeful, beginning again"},
    "sunny_islands": {"biome": "seaside", "weather": "fair", "palette": "summer", "layout": "islands",
                      "what": "islands in the sun - free, open, at ease"},
    "summer_coast": {"biome": "seaside", "weather": "fair", "palette": "summer", "layout": "village",
                     "what": "a fishing town on a summer coast - sociable, warm, busy in a good way"},
    "blossom_garden": {"biome": "botanical", "weather": "eternal_spring", "palette": "pastel", "layout": "plaza",
                       "nature": "garden", "what": "gardens in blossom - tender, cared for, things growing"},
    "golden_farmland": {"biome": "farmland", "weather": "fair", "palette": "autumn", "layout": "village",
                        "what": "golden fields at harvest - patient work paying off"},
    "sky_city": {"biome": "skycity", "weather": "fair", "palette": "pastel", "layout": "islands",
                 "what": "a city in the clouds - elated, proud, things going well"},
    "festival": {"biome": "funfair", "weather": "fair", "palette": "day", "layout": "plaza",
                 "nature": "funfair", "what": "a fair - joy, play, something to celebrate"},
    "toy_town": {"biome": "toytown", "weather": "fair", "palette": "pastel", "layout": "grid",
                 "what": "a town of toys - playful, light-hearted, curious as a child"},
    "new_year_lanterns": {"biome": "newyear", "weather": "eternal_night", "palette": "night", "layout": "plaza",
                          "what": "a lantern festival at the new year - together, homesick or home"},
    "christmas_village": {"biome": "christmas", "weather": "snowy", "palette": "winter", "layout": "village",
                          "what": "a snowy village at Christmas - cosy, grateful, near people"},
    "valentine_garden": {"biome": "valentine", "weather": "eternal_spring", "palette": "pastel", "layout": "plaza",
                         "what": "a garden of hearts - fond, in love, held close"},
    # calm, inward, thoughtful
    "misty_lake": {"biome": "lakeside", "weather": "foggy", "palette": "misty", "layout": "river",
                   "nature": "lake", "what": "a lake in mist - quiet, inward, thinking things over"},
    "still_lake_dawn": {"biome": "lakeside", "weather": "fair", "palette": "dawn", "layout": "river",
                        "nature": "lake", "what": "a still lake at dawn - calm, clear, a fresh start"},
    "rainy_forest": {"biome": "forest", "weather": "rainy", "palette": "day", "layout": "scattered",
                     "what": "a deep forest in soft rain - absorbed, heads-down, steady"},
    "quiet_grove": {"biome": "forest", "weather": "fair", "palette": "day", "layout": "village",
                    "nature": "grove", "what": "a quiet grove - rested, settled, at peace with the pace"},
    "waterfall_valley": {"biome": "highland", "weather": "changing", "palette": "day", "layout": "river",
                         "nature": "waterfall", "what": "a valley of waterfalls - a rush of energy, things flowing"},
    "library_town_rain": {"biome": "city", "weather": "rainy", "palette": "sepia", "layout": "grid",
                          "what": "an old town of libraries in the rain - studious, deep in reading"},
    "botanic_glasshouse": {"biome": "botanical", "weather": "foggy", "palette": "misty", "layout": "scattered",
                           "nature": "garden", "what": "glasshouses in the fog - nurturing something slowly"},
    "autumn_valley": {"biome": "autumn", "weather": "changing", "palette": "autumn", "layout": "river",
                      "what": "a valley in autumn - wistful, gathering in, looking back"},
    "enchanted_dusk": {"biome": "enchanted", "weather": "eternal_dusk", "palette": "dusk", "layout": "scattered",
                       "what": "an enchanted wood at dusk - dreamy, curious, inspired"},
    "starry_space": {"biome": "space", "weather": "eternal_night", "palette": "night", "layout": "islands",
                     "what": "islands among the stars - wonder, ambition, far from everything"},
    # UNDER THE SEA, IN THE SKY, IN SPACE - themes of their own (the user, 2026-11-20: "另外你现在要加水下/海底类的主题场景，
    # 和天空/云端的主题场景，还有太空/宇宙的主题场景，加上专属音乐，房子，区块和特色装饰，交通工具").
    "coral_reef": {"biome": "underwater", "weather": "fair", "palette": "day", "layout": "scattered",
                   "what": "a bright coral reef - lively, colourful, among friends"},
    "kelp_forest": {"biome": "underwater", "weather": "changing", "palette": "misty", "layout": "scattered",
                    "what": "a swaying kelp forest - drifting, thoughtful, sheltered"},
    "sunken_city": {"biome": "underwater", "weather": "foggy", "palette": "sepia", "layout": "plaza",
                    "what": "a sunken city of old - memories, something lost and found again"},
    "cloud_kingdom": {"biome": "skycity", "weather": "fair", "palette": "pastel", "layout": "islands",
                      "what": "castles on the clouds - light, dreamy, free"},
    "floating_isles": {"biome": "skycity", "weather": "changing", "palette": "dawn", "layout": "scattered",
                       "what": "floating islands at dawn - hopeful, a beginning, lifted up"},
    "sunset_sky": {"biome": "skycity", "weather": "eternal_dusk", "palette": "dusk", "layout": "islands",
                   "what": "a sky at sunset - tender, winding down, grateful"},
    "storm_clouds": {"biome": "skycity", "weather": "rainy", "palette": "misty", "layout": "scattered",
                     "what": "among storm clouds - restless, charged, pushing through"},
    "moon_base": {"biome": "space", "weather": "eternal_night", "palette": "night", "layout": "grid",
                  "what": "a base on the moon - focused, far away, building something new"},
    "nebula_garden": {"biome": "space", "weather": "eternal_night", "palette": "pastel", "layout": "scattered",
                      "what": "a garden in a nebula - wonder, creative, dreamy"},
    "ringed_planet": {"biome": "space", "weather": "eternal_night", "palette": "dusk", "layout": "islands",
                      "what": "a ringed planet - big plans, awe, a long way to go"},
    "orbital_station": {"biome": "space", "weather": "eternal_night", "palette": "night", "layout": "grid",
                        "what": "a station in orbit - busy, connected, working as a team"},
    # A CITY OF TODAY (the user, 2026-11-20: "还要加城市主题，里面有常见建筑，包括商场，公寓，摩天大楼，医院，警察局，消防局等等").
    "downtown": {"biome": "city", "weather": "fair", "palette": "day", "layout": "grid",
                 "what": "a busy downtown - ambitious, full days, things moving"},
    "harbour_city": {"biome": "city", "weather": "changing", "palette": "summer", "layout": "river",
                     "what": "a city by the harbour - open, ideas coming and going"},
    "quiet_suburbs": {"biome": "city", "weather": "fair", "palette": "spring", "layout": "grid",
                      "what": "quiet suburbs - settled, at home, steady routines"},
    "neon_metropolis": {"biome": "city", "weather": "eternal_night", "palette": "night", "layout": "grid",
                        "what": "a metropolis at night - electric, restless, working late"},
    "deep_sea_calm": {"biome": "underwater", "weather": "changing", "palette": "misty", "layout": "scattered",
                      "what": "the calm sea floor - deeply calm, unhurried, alone in a good way"},
    # driven, striving, under pressure
    "windy_highlands": {"biome": "highland", "weather": "changing", "palette": "dawn", "layout": "scattered",
                        "nature": "mountain", "what": "windy highlands at dawn - striving, reaching higher"},
    "snowy_peaks": {"biome": "snow", "weather": "snowy", "palette": "winter", "layout": "scattered", "nature": "peak",
                    "what": "snowy mountains in snow - a hard climb, a cold resolve"},
    "volcanoes": {"biome": "primeval", "weather": "eternal_dusk", "palette": "dusk", "layout": "scattered",
                  "nature": "volcano", "what": "a land of volcanoes - pressure, anger, a burst of fierce work"},
    "primeval_jungle": {"biome": "primeval", "weather": "rainy", "palette": "summer", "layout": "scattered",
                        "what": "a primeval jungle - wild, untamed, too much growing at once"},
    "night_city": {"biome": "city", "weather": "eternal_night", "palette": "night", "layout": "grid",
                   "what": "a city at night - restless, late hours, busy and bright"},
    "future_city": {"biome": "scifi", "weather": "fair", "palette": "night", "layout": "grid",
                    "what": "a city of the future - building fast, excited by what is coming"},
    "busy_market_plaza": {"biome": "city", "weather": "fair", "palette": "day", "layout": "plaza",
                          "what": "a market square at noon - many people, many asks, juggling"},
    "launch_site": {"biome": "scifi", "weather": "fair", "palette": "dawn", "layout": "scattered",
                    "what": "a launch site at dawn - on the edge of shipping something big"},
    "desert": {"biome": "desert", "weather": "fair", "palette": "dawn", "layout": "scattered",
               "what": "a desert - dry, drained, a long stretch with little rest"},
    "desert_oasis": {"biome": "desert", "weather": "fair", "palette": "dusk", "layout": "river",
                     "nature": "lake", "what": "an oasis in the desert - relief after a hard stretch"},
    # low, sad, tired, lost
    "snowfield": {"biome": "snow", "weather": "snowy", "palette": "winter", "layout": "scattered",
                  "what": "a snowfield under falling snow - low, sad, tired or alone"},
    "long_winter_village": {"biome": "snow", "weather": "eternal_winter", "palette": "winter", "layout": "village",
                            "what": "a village in a long winter - waiting it out, keeping warm inside"},
    "grey_rain_town": {"biome": "city", "weather": "rainy", "palette": "misty", "layout": "river",
                       "what": "a grey town in the rain - heavy, slow, going through the motions"},
    "foggy_ruins": {"biome": "ruins", "weather": "foggy", "palette": "sepia", "layout": "scattered",
                    "what": "old ruins in fog - something ended, missed or remembered"},
    "dark_sea_floor": {"biome": "underwater", "weather": "eternal_night", "palette": "night", "layout": "scattered",
                       "what": "the deep sea at night - under it all, overwhelmed"},
    "cavern_depths": {"biome": "cavern", "weather": "eternal_night", "palette": "night", "layout": "scattered",
                      "what": "deep caverns - withdrawn, digging through something alone"},
    "graveyard_mist": {"biome": "graveyard", "weather": "foggy", "palette": "dusk", "layout": "scattered",
                       "nature": "cemetery", "what": "a misty graveyard - grief, a loss, saying goodbye"},
    "haunted_autumn": {"biome": "halloween", "weather": "eternal_dusk", "palette": "dusk", "layout": "village",
                       "what": "a haunted town in autumn - anxious, uneasy, with a wry humour"},
    "lone_island": {"biome": "seaside", "weather": "foggy", "palette": "misty", "layout": "islands",
                    "what": "a lone island in fog - cut off, lonely, far from others"},
    # curious, new, on the move
    "zoo_park": {"biome": "zoo", "weather": "fair", "palette": "day", "layout": "plaza",
                 "what": "a park of animals - curious, outward, meeting the new"},
    "river_journey": {"biome": "meadow", "weather": "changing", "palette": "day", "layout": "river",
                      "what": "a land along a river - on the move, between places, in transition"},
}
#: EACH SCENE ITS OWN EDGE AND ITS OWN ANIMALS (the user, 2026-11-19: "每个场景应该都有自己的外围地形"; "不同场景的动物也
#:是unique的，每个场景外围要agent写2-3种地形，5个左右的动物，agent在确定场景之后从里面挑选"): the edge lands each scene may
#: run out in (api/web/play-regions.js PLAY_EDGES), and the animals that may live there - the agent choosing among them.
#: A scene with water round it ("sea") is its own: islands, a coast, a lake's shore.
EDGE_KINDS = ("sea", "thicket", "jungle", "snowdrift", "icecliff", "dunes", "mesa", "lava", "ashrock", "cliffs", "moor",
              "rubble", "graves", "hedges", "orchard", "fields", "autumnwood", "mushroomwood", "citywall", "industrial",
              "caverock", "toyland", "winterwood", "lanterns", "hearts", "reeds",
              # under the sea, in the sky, in space (2026-11-20)
              "coralreef", "kelp", "cloudbank", "starfield")
SCENE_LIFE: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "spring_meadow": (("hedges", "orchard", "moor"), ("rabbit", "sheep", "chicken", "squirrel", "deer")),
    "sunny_islands": (("sea",), ("turtle", "dolphin", "chicken", "cat", "fish1")),
    "summer_coast": (("sea", "cliffs"), ("cat", "dog", "turtle", "chicken", "dolphin")),
    "blossom_garden": (("orchard", "hedges", "hearts"), ("rabbit", "squirrel", "cat", "deer", "owl")),
    "golden_farmland": (("fields", "orchard", "hedges"), ("cow", "sheep", "chicken", "goat", "dog")),
    "sky_city": (("cloudbank", "cliffs", "hedges"), ("eagle", "owl", "robot1", "cat", "rabbit")),
    "festival": (("lanterns", "hedges", "toyland"), ("dog", "cat", "monkey", "rabbit", "chicken")),
    "toy_town": (("toyland", "hedges"), ("cat", "dog", "rabbit", "robot2", "monkey")),
    "new_year_lanterns": (("lanterns", "citywall", "orchard"), ("cat", "dog", "chicken", "tiger", "rabbit")),
    "christmas_village": (("winterwood", "snowdrift"), ("deer", "rabbit", "owl", "polarBear", "dog")),
    "valentine_garden": (("hearts", "orchard", "hedges"), ("rabbit", "cat", "deer", "squirrel", "owl")),
    "misty_lake": (("sea", "reeds", "moor"), ("turtle", "owl", "deer", "fish2", "rabbit")),
    "still_lake_dawn": (("sea", "reeds", "thicket"), ("deer", "turtle", "owl", "rabbit", "fish3")),
    "rainy_forest": (("thicket", "mushroomwood", "moor"), ("deer", "squirrel", "owl", "bear", "rabbit")),
    "quiet_grove": (("thicket", "hedges", "mushroomwood"), ("deer", "squirrel", "rabbit", "owl", "cat")),
    "waterfall_valley": (("cliffs", "thicket", "reeds"), ("deer", "eagle", "bear", "goat", "squirrel")),
    "library_town_rain": (("citywall", "hedges", "rubble"), ("cat", "owl", "dog", "squirrel", "rabbit")),
    "botanic_glasshouse": (("hedges", "jungle", "orchard"), ("monkey", "turtle", "rabbit", "squirrel", "owl")),
    "autumn_valley": (("autumnwood", "fields", "moor"), ("squirrel", "deer", "rabbit", "owl", "sheep")),
    "enchanted_dusk": (("mushroomwood", "thicket", "hearts"), ("wisp", "owl", "deer", "rabbit", "cat")),
    "starry_space": (("starfield", "caverock", "rubble"), ("robot1", "robot2", "robot3", "wisp", "eagle")),
    "deep_sea_calm": (("sea", "kelp", "reeds"), ("turtle", "dolphin", "fish1", "fish4", "orca")),
    "downtown": (("citywall", "industrial"), ("cat", "dog", "owl", "rabbit", "squirrel")),
    "harbour_city": (("sea", "citywall", "industrial"), ("cat", "dog", "dolphin", "owl", "squirrel")),
    "quiet_suburbs": (("hedges", "citywall"), ("dog", "cat", "rabbit", "squirrel", "owl")),
    "neon_metropolis": (("citywall", "industrial"), ("cat", "dog", "owl", "robot1", "rabbit")),
    "coral_reef": (("sea", "coralreef", "kelp"), ("fish1", "turtle", "dolphin", "fish4", "shark")),
    "kelp_forest": (("sea", "kelp", "reeds"), ("turtle", "fish1", "fish2", "orca", "dolphin")),
    "sunken_city": (("sea", "coralreef", "rubble"), ("turtle", "shark", "fish2", "fish4", "wisp")),
    "cloud_kingdom": (("cloudbank", "cliffs"), ("eagle", "owl", "rabbit", "cat", "deer")),
    "floating_isles": (("cloudbank", "cliffs", "hedges"), ("eagle", "goat", "rabbit", "deer", "owl")),
    "sunset_sky": (("cloudbank", "hedges"), ("eagle", "owl", "cat", "rabbit", "squirrel")),
    "storm_clouds": (("cloudbank", "cliffs", "rubble"), ("eagle", "owl", "goat", "wisp", "robot1")),
    "moon_base": (("starfield", "caverock"), ("robot1", "robot2", "robot3", "wisp", "cat")),
    "nebula_garden": (("starfield", "mushroomwood"), ("wisp", "robot1", "owl", "rabbit", "deer")),
    "ringed_planet": (("starfield", "caverock", "rubble"), ("robot1", "robot2", "robot3", "wisp", "eagle")),
    "orbital_station": (("starfield", "industrial"), ("robot1", "robot2", "robot3", "cat", "dog")),
    "windy_highlands": (("cliffs", "moor", "icecliff"), ("goat", "eagle", "sheep", "deer", "bear")),
    "snowy_peaks": (("icecliff", "snowdrift", "winterwood"), ("polarBear", "goat", "eagle", "owl", "deer")),
    "volcanoes": (("lava", "ashrock", "cliffs"), ("croc", "eagle", "rhino", "goat", "turtle")),
    "primeval_jungle": (("jungle", "thicket", "reeds"), ("gorilla", "monkey", "tiger", "croc", "elephant")),
    "night_city": (("citywall", "industrial"), ("cat", "dog", "owl", "robot1", "rabbit")),
    "future_city": (("industrial", "citywall", "hedges"), ("robot1", "robot2", "robot3", "cat", "dog")),
    "busy_market_plaza": (("citywall", "lanterns", "orchard"), ("cat", "dog", "chicken", "goat", "monkey")),
    "launch_site": (("industrial", "dunes", "mesa"), ("robot2", "robot3", "eagle", "camel", "dog")),
    "desert": (("dunes", "mesa"), ("camel", "lion", "croc", "eagle", "turtle")),
    "desert_oasis": (("dunes", "reeds", "jungle"), ("camel", "giraffe", "zebra", "elephant", "turtle")),
    "snowfield": (("snowdrift", "winterwood", "icecliff"), ("polarBear", "rabbit", "owl", "deer", "goat")),
    "long_winter_village": (("winterwood", "snowdrift", "citywall"), ("dog", "cat", "deer", "owl", "polarBear")),
    "grey_rain_town": (("citywall", "rubble", "moor"), ("cat", "dog", "owl", "squirrel", "rabbit")),
    "foggy_ruins": (("rubble", "moor", "mushroomwood"), ("owl", "wisp", "goat", "squirrel", "deer")),
    "dark_sea_floor": (("sea", "caverock"), ("shark", "orca", "fish4", "turtle", "fish2")),
    "cavern_depths": (("caverock", "ashrock", "rubble"), ("wisp", "owl", "bear", "robot3", "turtle")),
    "graveyard_mist": (("graves", "moor", "mushroomwood"), ("owl", "wisp", "cat", "rabbit", "deer")),
    "haunted_autumn": (("graves", "autumnwood", "mushroomwood"), ("owl", "cat", "wisp", "squirrel", "dog")),
    "lone_island": (("sea", "cliffs"), ("turtle", "eagle", "goat", "dolphin", "cat")),
    "zoo_park": (("hedges", "jungle", "fields"), ("giraffe", "elephant", "zebra", "lion", "hippo")),
    "river_journey": (("reeds", "moor", "thicket"), ("deer", "sheep", "rabbit", "dog", "turtle")),
}
#: THE WAYS ARE PART OF THE DESIGN (the user, 2026-11-19: "对道路的选择，也是设计的一部分 ... 道路的材质，和宽度（允许多格
#: 道路），弯曲程度都是设计的一部分"): what a way may be made of (api/web ground tiles), how wide, how much it bends.
ROAD_MATERIALS = ("dirt", "dirt2", "cobble", "cobble2", "paving", "pavement2", "asphalt", "stone", "sand", "bridge",
                  "toyPath", "toyStone", "caveSand")
ROAD_WIDE_MOST = 3


def road_of(said: Any) -> Optional[Dict[str, Any]]:
    """A way as designed, checked: a material of the list, one to three tiles wide, its bend from 0 (straight) to 1."""
    if not isinstance(said, dict) or str(said.get("material") or "") not in ROAD_MATERIALS:
        return None
    try:
        width = max(1, min(ROAD_WIDE_MOST, int(said.get("width") or 1)))
    except (TypeError, ValueError):
        width = 1
    try:
        curve = max(0.0, min(1.0, float(said.get("curve") if said.get("curve") is not None else 0.4)))
    except (TypeError, ValueError):
        curve = 0.4
    return {"material": str(said["material"]), "width": width, "curve": round(curve, 2)}


def life_of(scene: str, said: Any) -> Dict[str, List[str]]:
    """The edges and animals the agent chose for its land, each of the scene's own - all of them, when it chose none."""
    edges, animals = SCENE_LIFE.get(scene, ((), ()))
    said = said if isinstance(said, dict) else {}
    chosen_edges = [one for one in (said.get("edges") or []) if one in edges][:3] or list(edges)
    if "sea" in edges and "sea" not in chosen_edges:  # an island, a coast, a lake: its water always (the user, 2026-11-19)
        chosen_edges = ["sea", *chosen_edges][:3]
    chosen_animals = [one for one in (said.get("animals") or []) if one in animals][:5] or list(animals)
    return {"edges": chosen_edges, "animals": chosen_animals}


#: The world outside opens on a feeling read from this much at least - their words and their days, counted together.
FIRST_LAND_EVIDENCE = 8
#: A new land for a new feeling only after the last has stood this many days - not one each growth (the user,
#: 2026-11-20: "这个改成3天就行"; then "每隔1天就允许变化").
MOOD_LAND_DAYS = 1                  # 2026-11-20: "那个心情，每隔1天就允许变化（而不是3天）"
#: How many of their words, and of the feelings read before, the call is told.
MOOD_WORDS_MOST, MOODS_KEPT, MOODS_TOLD = 60, 60, 6

#: THE FEELING IN TWO STEPS (the user, 2026-11-19: "agent看了这个心情之后写的一段话，然后根据这段话再选地区，而不是直接生成
#: 心情词（一段话表达内容更多一点）"): first a paragraph, from their days alone; then the land, from that paragraph alone.
TASK_MOOD_SCENE = "play_world_mood_scene"
MOOD_SYSTEM = """You keep a small world that pictures the user's life. Each stretch of their days becomes a land, and \
the land is how those days FELT - not what they did. Read, gently, their own words in their chats ("their_words") \
and the rhythm of their days ("their_rhythm": how long, how late, how scattered).
THEIR FIELD'S WEATHER TOO (the user, 2026-11-20: "因为user的心情可能是比较稳定的，找当时user主要activity的领域（比如AI, \
篮球之类）在那个时候的大事件，infer整体的心情"): from their work then ("their_work") and their words, name the one to \
three FIELDS their days were spent in - broad public fields (AI, economics research, basketball, a game), never \
their own projects - and SEARCH THE WEB for the big events in each of those fields in exactly these days \
("these_days"): what happened, and how the field felt - in a boom, in turmoil, in mourning, celebrating, anxious, \
quiet. Search only with the field's public name and the dates; nothing of theirs ever goes into a search.
Then WRITE HOW THESE DAYS FELT: a paragraph of three to six sentences, in your own warm words - their mood and how it \
moved through the days, AND the mood of their field around them and how the two met (they were well while their \
field was in a storm; their field was celebrating while they were tired) - its weather, its light, what it was like \
to be them now. Not a label, not a list of words: the paragraph is all that will be passed on, so let it hold \
everything. Never a diagnosis; never name another person.
Answer with only JSON: {{"mood": "<the paragraph>", "fields": [{{"field": "<a public field>", "events": "<its big \
events of these days, at most 2 sentences>", "feeling": "<how the field felt, a few words>"}}, ...up to 3]}}"""
MOOD_SCENE_SYSTEM = """You keep a small world that pictures the user's life. Each stretch of their days becomes a land, \
and the land is how those days felt. Here is how these days felt, as written ("mood"), and how the stretches before \
felt and what lands they became ("before"). The paragraph holds THEIR feeling and their FIELD's (the big events of \
the field they worked in, and how it felt): choose the land from both and how they met - a warm lit house in a \
storm, a quiet valley while the city beyond celebrates (the user: "结合user和领域的心情（比如user很好，领域很糟）进行主题选择和 \
设计"). IMAGINE ITS LAND, IN TWO STEPS: first THE THEME this paragraph belongs to, then, of that theme's scenes, the \
one it would be if it were a place (the lie of the land - islands, a valley, a city - its weather and its light come \
with it). Only the scenes open now are listed - one used rests till the rest of its theme have been. EACH THEME AND \
SCENE SAYS HOW MANY LANDS HAVE BEEN IT ALREADY (the user: "主题的选择也尽可能选没用过的主题"): where the feeling allows, \
choose a theme used least - one never used best - so the world keeps finding new lands:
{scenes}
Answer with only JSON: {{"theme": "<the theme's name>", "scene": "<one of that theme's scenes>", "land_name": "<the land's own name, a few words, a \
poet's not a label>", "why": "<to them, in the first person, at most 2 sentences: what in the paragraph made you see \
this land - kind, never a diagnosis>", "feeling": "<how these days felt, in a few words - their land's title says it \
(the user: "每个区域的title, 要加上用户心情，和agent说的话，比如安慰什么的")>", "to_them": "<what you say to them now, as \
a friend who knows these days - comfort when they were hard, a hand on the shoulder when tired, gladness with them \
when they were good; at most 2 sentences>", "look": "<what this land is built of and in what style - its walls, \
floors and things, outside and in: at most 2 sentences; every room in it follows it>", "changed": true or false (unlike the stretch before - A SMALL SWING COUNTS TOO, the \
user: "心情小起伏也行，变的更频繁一点挺好的"; false only when it felt the same), "music": the land's music, as the paragraph sounds (the user: "音乐也根据心情选") - {{"track": one of \
{tracks}}} only where a recorded piece truly is that feeling, else your own tune: {{"tempo": 50-160 (slow for a low or \
calm feeling, quick for a bright or driven one), "instrument": one of {instruments}, "accompaniment": one of \
{instruments}, "style": one of {arrangements}, "mode": "major" or "minor", "melody": "<8-32 eighth notes, C5 F#4 Bb5 or \
- for a rest>", "bass": "<4-8 notes, one a bar>"}}}}, "edges": [1 to 3 of the scene's edges - what the land runs out \
in], "animals": [3 to 5 of the scene's animals - who lives there], "road": {{"material": one of {roads}, "width": 1 to \
3, "curve": 0 (straight) to 1 (winding)}} - the land's main ways, as the feeling walks."""


def _local(stamp: Any) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone()


def feelings_between(db: Any, since: str, until: str, most: int = MOOD_WORDS_MOST) -> Dict[str, List[str]]:
    """WHAT THE FEELING IS READ FROM, and only this (the user chose: "聊天里的话, 任务和工作节奏"): their own words in
    their chats, spread over the days; each day's rhythm - how many moments the agent saw them at work, from when to
    when, how many after eleven at night. Read on this computer, kept in its world, sent nowhere else."""
    def rows(sql: str, *args: Any) -> List[Any]:
        try:
            return list(db.query(sql, args))
        except Exception:
            return []
    said = rows("SELECT created_at, content FROM messages WHERE role = 'user' AND created_at >= ? AND created_at < ? "
                "ORDER BY created_at", since, until)
    step = max(1, len(said) // max(1, most))
    words = []
    for one in said[::step][:most]:
        when = _local(one["created_at"])
        text = " ".join(str(one["content"] or "").split())[:200]
        if text:
            words.append(f"{when:%Y-%m-%d %H:%M} {text}" if when else text)
    days: Dict[str, List[datetime]] = {}
    for one in rows("SELECT created_at FROM states WHERE about = 'user' AND created_at >= ? AND created_at < ? "
                    "ORDER BY created_at", since, until):
        when = _local(one["created_at"])
        if when:
            days.setdefault(f"{when:%Y-%m-%d}", []).append(when)
    rhythm = []
    for day in sorted(days):
        times = days[day]
        late = sum(1 for t in times if t.hour >= 23 or t.hour < 5)
        rhythm.append(f"{day}: {len(times)} moments at work, {min(times):%H:%M} to {max(times):%H:%M}"
                      + (f", {late} after 11 pm" if late else ""))
    return {"their_words": words, "their_rhythm": rhythm}


#: THE SCENES IN THEMES (the user, 2026-11-19: "把几十个场景分成几个大的主题，model先选主题，再选场景，然后一个主题里的场景
#: 用过就停用，直到主题里所有场景都被用过再启用，这样能使得整体场景diversity最大化"): every scene in exactly one.
SCENE_THEMES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "growing": ("things growing, tender and new - spring, gardens, harvest",
                ("spring_meadow", "blossom_garden", "valentine_garden", "golden_farmland", "botanic_glasshouse")),
    "water": ("by the water - islands, coasts, lakes, a river",
              ("sunny_islands", "summer_coast", "misty_lake", "still_lake_dawn", "lone_island", "river_journey")),
    "woods": ("in the woods and the wild - forests, groves, jungle, animals",
              ("rainy_forest", "quiet_grove", "enchanted_dusk", "primeval_jungle", "autumn_valley", "zoo_park")),
    "heights": ("high and cold - highlands, peaks, waterfalls, snow, winter",
                ("windy_highlands", "snowy_peaks", "waterfall_valley", "snowfield", "long_winter_village",
                 "christmas_village")),
    "town": ("among people - towns and cities, by day and by night",
             ("library_town_rain", "night_city", "busy_market_plaza", "grey_rain_town", "future_city")),
    "celebration": ("celebration and play - fairs, toys, lanterns, a haunted town",
                    ("festival", "toy_town", "new_year_lanterns", "haunted_autumn")),
    "far": ("far and deep - caverns, a launch",
            ("cavern_depths", "launch_site")),
    # A CITY OF TODAY (the user, 2026-11-20): its malls, flats, towers, hospital, police and fire stations.
    "city": ("a city of today - downtown, the harbour, the suburbs, the city at night",
             ("downtown", "harbour_city", "quiet_suburbs", "neon_metropolis")),
    # THEMES OF THEIR OWN (the user, 2026-11-20): under the sea, in the sky, in space.
    "undersea": ("under the sea - reefs, kelp, a sunken city, the deep",
                 ("coral_reef", "kelp_forest", "sunken_city", "deep_sea_calm", "dark_sea_floor")),
    "sky": ("in the sky - clouds, floating islands, sunsets, storms",
            ("cloud_kingdom", "sky_city", "floating_isles", "sunset_sky", "storm_clouds")),
    "cosmos": ("in space - stars, the moon, a nebula, a ringed planet, a station in orbit",
               ("starry_space", "moon_base", "nebula_garden", "ringed_planet", "orbital_station")),
    "harsh": ("hard ground - volcanoes, desert, ruins, a graveyard",
              ("volcanoes", "desert", "desert_oasis", "foggy_ruins", "graveyard_mist")),
}


def theme_of(scene: str) -> str:
    return next((name for name, (_what, scenes) in SCENE_THEMES.items() if scene in scenes), "")


def scenes_open(used: List[str], keep: str = "") -> Dict[str, List[str]]:
    """THE SCENES THAT MAY BE CHOSEN NOW, BY THEME: each theme's scenes not used since the theme last came round - a
    theme all of whose scenes have been used beginning again; `used` the lands' scenes, the oldest first. The scene of
    the land the feeling is in now (`keep`) may always be chosen again - the same feeling, the same land."""
    rested: Dict[str, set] = {name: set() for name in SCENE_THEMES}
    for scene in used:
        theme = theme_of(scene)
        if not theme:
            continue
        rested[theme].add(scene)
        if rested[theme] >= set(SCENE_THEMES[theme][1]):
            rested[theme] = set()                      # every one used: the theme comes round again
    return {name: [s for s in scenes if s not in rested[name] or s == keep]
            for name, (_what, scenes) in SCENE_THEMES.items()}


#: EACH OF THESE THEMES' OWN HOUSES, GROUNDS AND THINGS (the user, 2026-11-20: "加上专属音乐，房子，区块和特色装饰，交通工具"):
#: told with its scene, kept with its land ("own"), read by every design there.
SCENE_OWN: Dict[str, str] = {
    "downtown": "skyscrapers, a shopping mall, a hospital, a police station, a fire station, offices, crossings, bus stops",
    "harbour_city": "apartment blocks by the water, a shopping mall, a hospital, a fire station by the docks, cranes, a "
                    "fish market, a ferry pier",
    "quiet_suburbs": "houses with gardens, apartment blocks, a school, a clinic, a small police station, a fire station, "
                     "a supermarket, a park",
    "neon_metropolis": "glowing skyscrapers, a night mall, a hospital lit all night, police and fire stations, neon signs, "
                       "food stalls",
    "coral_reef": "houses of coral and shell; coral arches, giant clams, treasure chests, bubbling vents; a submarine dock",
    "kelp_forest": "houses in kelp, lantern fish, sea glass windows; swaying kelp groves, sunken boats; a submarine dock",
    "sunken_city": "domes of an old city under the sea, broken columns, mosaics, statues with fish; glass tunnels",
    "deep_sea_calm": "pearl houses on the sand, sea anemone gardens, shells, glowing jellyfish lamps",
    "dark_sea_floor": "glowing deep-sea houses, anglerfish lights, black smokers, a lone research sub",
    "cloud_kingdom": "castles of cloud and marble, rainbow bridges, wind chimes, hot-air balloons tied up",
    "sky_city": "towers on the clouds, sky gardens, flags, airship docks",
    "floating_isles": "houses on floating rocks with waterfalls falling off them, rope bridges, kites",
    "sunset_sky": "pavilions on pink clouds, lanterns, birds, a balloon going home",
    "storm_clouds": "lightning towers, wind mills, storm-glass lamps, rain catchers",
    "starry_space": "domes among the stars, telescopes, comets, crystals",
    "moon_base": "white domes and modules, solar panels, rovers, a landing pad, craters",
    "nebula_garden": "glass greenhouses in coloured gas, star flowers, crystal trees, floating orbs",
    "ringed_planet": "rings in the sky, observatory towers, rock spires, a spaceport",
    "orbital_station": "modules joined by tubes, docking arms, windows on the stars, satellites",
}


def mood_scene_system(tracks: str = "", instruments: str = "", arrangements: str = "",
                      open_now: Optional[Dict[str, List[str]]] = None, used: Optional[List[str]] = None) -> str:
    """The land's prompt: the scenes open now by theme - each theme and scene with how many lands have been it
    (`used`: the lands' scenes; not forced, told - the user, 2026-11-20: "尽管不用代码强制，但是要列出每个主题已经使用的次数")."""
    open_now = open_now or scenes_open([])
    used = list(used or [])
    life = lambda name: (f"(edges: {', '.join(SCENE_LIFE.get(name, ((), ()))[0])}; animals: "   # noqa: E731
                         f"{', '.join(SCENE_LIFE.get(name, ((), ()))[1])})")
    times = lambda n: "never used" if not n else f"used {n} time{'s' if n > 1 else ''}"   # noqa: E731
    listed = "\n".join(
        f"THEME {theme} - {SCENE_THEMES[theme][0]} [{times(sum(1 for s in used if theme_of(s) == theme))}]:\n"
        + "\n".join(f"  - {name} [{times(used.count(name))}]: {MOOD_SCENES[name]['what']} {life(name)}"
                     + (f" - its own: {SCENE_OWN[name]}" if name in SCENE_OWN else "")
                     for name in scenes)
        for theme, scenes in open_now.items() if scenes)
    return MOOD_SCENE_SYSTEM.format(scenes=listed, roads=", ".join(ROAD_MATERIALS),
                                    tracks=tracks, instruments=instruments, arrangements=arrangements,
                                    outlines=", ".join(OUTLINES))


def read_mood(found: Any, mood: str = "", open_now: Optional[Dict[str, List[str]]] = None) -> Optional[Dict[str, Any]]:
    """The land the paragraph became, as the model said it, checked: a scene of the list, or nothing - with the
    paragraph it came of (`mood`). A scene resting (`open_now`: what may be chosen) given: the theme's first open one,
    else nothing."""
    if not isinstance(found, dict) or str(found.get("scene") or "") not in MOOD_SCENES:
        return None
    if open_now is not None and not any(found["scene"] in scenes for scenes in open_now.values()):
        theme = theme_of(str(found["scene"]))
        if not open_now.get(theme):
            return None
        found = {**found, "scene": open_now[theme][0]}
    short = lambda text, n: " ".join(str(text or "").split())[:n]       # noqa: E731
    return {"mood": short(mood or found.get("mood"), 1200), "scene": str(found["scene"]),
            "theme": theme_of(str(found["scene"])),
            "land_name": short(found.get("land_name"), 60), "why": short(found.get("why"), 300),
            **{k: short(found.get(k), n) for k, n in (("feeling", 80), ("to_them", 300), ("look", 300))
               if short(found.get(k), n)},
            # no outline: a land is never a ruled figure (2026-11-20: "主题地块一定是不规则的")
            "changed": bool(found.get("changed")),
            **({"music": found["music"]} if isinstance(found.get("music"), dict) else {}),
            **life_of(str(found["scene"]), found),
            **({"road": road_of(found.get("road"))} if road_of(found.get("road")) else {})}


def scene_settings(scene: str) -> Dict[str, str]:
    """What a scene sets of a land - its biome, weather, light and lie - the code's, whatever else was said."""
    one = MOOD_SCENES.get(scene) or {}
    return {key: one[key] for key in ("biome", "weather", "palette", "layout") if key in one}


# -- 2. the muses ----------------------------------------------------------------------------------------------------
MUSE_SYSTEM = """You keep a small world that pictures the user's life, and before its places are designed you find \
each project's MUSES: A POEM, A PIECE OF MUSIC, A PAINTING and A VIDEO that hold what the project is. Each may be one you find - search the web: a real work, its real title, maker \
and address, never made up - or one you make yourself: a poem you write, a tune you compose, a painting you draw. \
Choose what truly moves, not the obvious; let the four answer one another. Then say what the four ask of its places - \
their colours, their light, their textures, what stands where, the moment someone walking in should feel.
Answer with only JSON: {{"muses": [{{"project": "<the project's name as given>",
 "poem": {{"title": "...", "by": "<the poet, or 'your agent'>", "lines": ["<your own poem whole, at most 8 lines; a \
found one's lines only if it is in the public domain, else none>"], "url": "<a found one's address>", \
"public_domain": true or false}},
 "music": {{"title": "...", "by": "...", "url": "...", "public_domain": true or false, "tune": {{"tempo": 50-160, \
"instrument": one of {instruments}, "accompaniment": one of {instruments}, "style": one of {arrangements}, "mode": \
"major" or "minor", "melody": "<8-32 eighth notes, C5 F#4 Bb5 or - for a rest, space-separated>", "bass": "<4-8 \
notes, one a bar>"}} (always: your own tune in its spirit, played in the project's place)}},
 "painting": {{"title": "...", "by": "...", "year": "...", "url": "...", "public_domain": true or false, "svg": \
"<your own painting of it, or after it, as an SVG on viewBox 0 0 128 128 - shapes, paths and gradients only, rich \
and finely drawn: it is hung in the project's place as fine pixel art>", "pixels": {{"palette": ["#rrggbb", ...up to \
10], "rows": ["<32 rows of 32: a digit 0-9 for a palette colour, . for none>"]}} (the same, small, in case)}},
 "video": {{"title": "...", "by": "...", "url": "<its address>", "why": "<one sentence>"}},
 "design": "<what the four ask of its places, at most 4 sentences>", "colours": ["#rrggbb", "...3 to 6 the four \
share"]}}]}}"""
#: How large the painting drawn after the muse is (pixel_art_large: 2 x 2 tiles).
PAINTING_PX = (32, 32)


def _url(text: Any) -> str:
    text = str(text or "").strip()
    return text[:400] if re.match(r"https?://[^\s\"'<>]+$", text) else ""


def read_muses(found: Any, projects: Iterable[str], tune_of: Callable[[Any], Any],
               drawn_pixels: Callable[..., Any]) -> Dict[str, Dict[str, Any]]:
    """{project name: its muses}, checked: addresses that are addresses, a found poem's lines only when the public's,
    a tune that is a tune, a painting that is drawn - what fails, left out."""
    names = {str(n).lower(): str(n) for n in projects}
    out: Dict[str, Dict[str, Any]] = {}
    short = lambda text, n: " ".join(str(text or "").split())[:n]       # noqa: E731
    for one in (found.get("muses") if isinstance(found, dict) else None) or []:
        if not isinstance(one, dict) or str(one.get("project") or "").lower() not in names:
            continue
        muse: Dict[str, Any] = {}
        poem = one.get("poem") if isinstance(one.get("poem"), dict) else {}
        own = str(poem.get("by") or "").strip().lower() in ("your agent", "the agent", "agent", "")
        lines = [short(l, 80) for l in (poem.get("lines") or []) if short(l, 80)][:8] \
            if own or poem.get("public_domain") is True else []
        if short(poem.get("title"), 80) and (lines or _url(poem.get("url"))):
            muse["poem"] = {"title": short(poem.get("title"), 80), "by": short(poem.get("by"), 60) or "your agent",
                            "lines": lines, "url": _url(poem.get("url"))}
        music = one.get("music") if isinstance(one.get("music"), dict) else {}
        tune = tune_of(music.get("tune"))
        if short(music.get("title"), 80) or tune:
            muse["music"] = {"title": short(music.get("title"), 80), "by": short(music.get("by"), 60),
                             "url": _url(music.get("url")), **({"tune": tune} if tune else {})}
        painting = one.get("painting") if isinstance(one.get("painting"), dict) else {}
        pixels = drawn_pixels(painting.get("pixels"), *PAINTING_PX)
        from .playpieces import clean_svg
        svg = clean_svg(painting.get("svg"), 128, 128) if painting.get("svg") else None
        if short(painting.get("title"), 80) or pixels or svg:
            muse["painting"] = {"title": short(painting.get("title"), 80), "by": short(painting.get("by"), 60),
                                "year": short(painting.get("year"), 12), "url": _url(painting.get("url")),
                                **({"pixels": pixels} if pixels else {}), **({"svg": svg} if svg else {})}
        video = one.get("video") if isinstance(one.get("video"), dict) else {}
        if _url(video.get("url")) and short(video.get("title"), 80):
            muse["video"] = {"title": short(video.get("title"), 80), "by": short(video.get("by"), 60),
                             "url": _url(video.get("url")), "why": short(video.get("why"), 200)}
        design = short(one.get("design"), 600)
        if design:
            muse["design"] = design
        colours = [c for c in (one.get("colours") or []) if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c)]
        if colours:
            muse["colours"] = colours[:6]
        if muse:
            out[names[str(one["project"]).lower()]] = muse
    return out


# -- 2b. each task's work as a tree of cards -------------------------------------------------------------------------
# THE WORK TREE IN THE WORLD (the user, 2026-11-19: "你之前work panel不是有一个把project变成work tree的方式吗，你把每个project
# 都变成work tree, 然后起码每个card都要有一个（或者多个item)代表"; "这个工作树可以保留，允许复用（对每个树的task, 自己生成)"):
# a task's cards as the Work panel keeps them (read, never written); a task with none, a tree the world makes for it
# once and keeps. Every card stands for at least one thing; a deliverable with parts, a place of its own.
CARD_STATUSES = ("open", "working", "done", "set_aside")
#: A task's tree as the world uses it: at most this many cards (the deepest let go first).
TREE_MOST = 160
#: How many tasks without a tree are given one in a call; how many cards each.
TREES_A_CALL, TREE_MADE_MOST = 12, 30
#: A card's kind of work, the thing that stands for it when the room's design left it out.
CARD_THINGS = {"code": "computer", "document": "bookshelf", "slides": "whiteboard", "data": "cabinet",
               "email": "wall_shelf", "design": "painting", "research": "bookcase", "plan": "map", "meeting": "table",
               "test": "desk", "release": "crate"}

TREE_SYSTEM = """You keep a small world that pictures the user's life; each task in it is shown by its work, as a \
tree of cards. These tasks have no tree yet - make one for each from what is known of it. A CARD is a piece of the \
deliverable: what must be true when it is done. The ROOT is the work's goal; the cards under it are the \
deliverables the user would recognise; the cards under those are their parts, each one output. Only what the work \
truly had or has - never made up; a small task may be a root and two or three cards. Status: "done" what was done, \
"working" what was under way when it was last seen, "open" what is still to do, "set_aside" what was left.
Answer with only JSON: {{"trees": {{"<task id>": [{{"id": "<c1, c2, ...>", "parent": "<its parent's id, '' for \
the root>", "title": "<a few words>", "statement": "<what is true when it is done, one sentence>", "type": "<code, \
document, slides, data, email, design, research, plan, meeting, test, release or other>", "status": "..."}}, ...up \
to {most}]}}}}"""


def work_cards(db: Any, task_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """THE WORK PANEL'S CARDS OF THESE TASKS, READ ONLY: {task id: [{"id", "parent", "title", "statement", "type",
    "status"}]} - a task's own, and those of the tree it is the root of."""
    wanted = [str(one) for one in task_ids if str(one)]
    if not wanted:
        return {}
    holes = ", ".join("?" for _ in wanted)
    try:
        rows = list(db.query(
            f"SELECT id, unit_id, task_id, parent_id, type, title, statement, status FROM experience_cards "
            f"WHERE scope = 'live' AND (task_id IN ({holes}) OR unit_id IN ({holes})) AND replaced_by = '' "
            f"ORDER BY created_at", (*wanted, *wanted)))
    except Exception:
        return {}
    # WHAT EACH CARD TOOK (the user, 2026-11-19: "featured step (特别难的，花了特别多时间的步骤)"; "progress (第一阶段成果，
    # 第二阶段成果)"): the stages of work done on it - how long (each at most STAGE_HOURS_MOST hours: a stage left open
    # for days was not worked all that while), how many tries, how many of them a putting right, what the last one made.
    worked: Dict[str, Dict[str, Any]] = {}
    ids = [str(row["id"]) for row in rows]
    for n in range(0, len(ids), 400):
        part = ids[n:n + 400]
        try:
            stages = list(db.query(
                f"SELECT card_id, kind, began, ended, deliverable, summary FROM experience_stages "
                f"WHERE scope = 'live' AND card_id IN ({', '.join('?' for _ in part)}) ORDER BY began", tuple(part)))
        except Exception:
            stages = []
        for stage in stages:
            one = worked.setdefault(str(stage["card_id"]), {"minutes": 0.0, "tries": 0, "reworks": 0, "result": "",
                                                            "first": ""})
            began, ended = _local(stage["began"]), _local(stage["ended"])
            if began and (not one["first"] or began.date().isoformat() < one["first"]):
                one["first"] = began.date().isoformat()
            if began and ended and ended > began:
                one["minutes"] += min((ended - began).total_seconds() / 60, STAGE_HOURS_MOST * 60)
            one["tries"] += 1
            one["reworks"] += 1 if str(stage["kind"] or "") == "adjustment" else 0
            made = " ".join(str(stage["deliverable"] or stage["summary"] or "").split())
            if made:
                one["result"] = made[:240]
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        took = worked.get(str(row["id"])) or {}
        for task in {str(row["task_id"] or ""), str(row["unit_id"] or "")} & set(wanted):
            out.setdefault(task, []).append({
                "id": str(row["id"]), "parent": str(row["parent_id"] or ""),
                "title": " ".join(str(row["title"] or "").split())[:80],
                "statement": " ".join(str(row["statement"] or "").split())[:300],
                "type": str(row["type"] or ""), "status": str(row["status"] or "open"),
                **({"minutes": round(took["minutes"]), "tries": took["tries"], "reworks": took["reworks"],
                    "first": took["first"]} if took.get("tries") else {}),
                **({"result": took["result"]} if took.get("result") else {})})
    return out


#: A task's progress in this many stages at most.
STAGES_MOST = 8
#: A stage counted at most this many hours of work (one left open for days was not worked all that while).
STAGE_HOURS_MOST = 6
#: A task's featured steps: at most this many, each of at least FEATURED_MINUTES_LEAST minutes or FEATURED_TRIES_LEAST
#: tries and well above the task's usual (FEATURED_OVER times its middle card's minutes).
FEATURED_MOST, FEATURED_MINUTES_LEAST, FEATURED_TRIES_LEAST, FEATURED_OVER = 3, 90, 3, 2.0


def featured_steps(cards: List[Dict[str, Any]]) -> List[str]:
    """THE STEPS THAT STOOD OUT (the user: "特别难的，花了特别多时间的步骤"): a task's cards that took far longer than its
    others, or many tries, or were put right again and again - the ids, the most first."""
    timed = sorted(float(c.get("minutes") or 0) for c in cards if c.get("minutes"))
    middle = timed[len(timed) // 2] if timed else 0.0
    weight = lambda c: float(c.get("minutes") or 0) + 60 * int(c.get("reworks") or 0)    # noqa: E731
    stood = [c for c in cards if (float(c.get("minutes") or 0) >= max(FEATURED_MINUTES_LEAST, FEATURED_OVER * middle))
             or int(c.get("tries") or 0) >= FEATURED_TRIES_LEAST or int(c.get("reworks") or 0) >= 2]
    return [c["id"] for c in sorted(stood, key=weight, reverse=True)[:FEATURED_MOST]]


def stages_of(cards: List[Dict[str, Any]], groups: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """A TASK'S PROGRESS BY ITS STAGES (the user: "第一阶段成果，第二阶段成果"): each stage how far along - the share of its
    cards done, its status, what it made. The stages: `groups` as the design gave them ([{"title", "cards": [ids]}] -
    most trees are flat, a root with dozens of cards under it, and only a reading of them sees where one stage ends);
    else the tree's own first level, when it truly is a few stages each with work under it; else none."""
    by_id = {c["id"]: c for c in cards}
    if groups:
        out = []
        for n, group in enumerate(groups[:STAGES_MOST]):
            mine = [by_id[i] for i in group.get("cards") or [] if i in by_id]
            if not mine:
                continue
            done = sum(1 for c in mine if c.get("status") == "done")
            last = [c["result"] for c in mine if c.get("result") and c.get("status") == "done"]
            out.append({"n": len(out) + 1, "title": " ".join(str(group.get("title") or "").split())[:60],
                        "cards": [c["id"] for c in mine], "done": done, "of": len(mine),
                        "status": "done" if done == len(mine) else "working" if done or any(
                            c.get("status") == "working" for c in mine) else "open",
                        **({"result": last[-1]} if last else {})})
        return out
    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for one in cards:
        by_parent.setdefault(str(one.get("parent") or ""), []).append(one)
    def under(card_id: str) -> List[Dict[str, Any]]:
        out, todo = [], list(by_parent.get(card_id, []))
        while todo:
            one = todo.pop()
            out.append(one)
            todo.extend(by_parent.get(one["id"], []))
        return out
    first = [c for c in cards if int(c.get("depth") or 0) == 1]
    if not (2 <= len(first) <= STAGES_MOST and all(by_parent.get(c["id"]) for c in first)):
        return []
    out = []
    for n, one in enumerate(first):
        parts = under(one["id"]) or [one]
        done = sum(1 for c in parts if c.get("status") == "done")
        out.append({"n": n + 1, "card": one["id"], "title": one.get("title", ""), "status": one.get("status", "open"),
                    "done": done, "of": len(parts), **({"result": one["result"]} if one.get("result") else {})})
    return out


# -- THE WORLD THEN: what happened in the world while each piece of work was done ------------------------------------
TASK_WORLD_EVENTS = "play_world_events"
WORLD_EVENTS_SYSTEM = """You keep a small world that pictures the user's life, and beside each piece of their work it \
shows WHAT HAPPENED IN THE WORLD WHILE THEY DID IT (the user, 2026-11-19: "在你做这个project的时候，世界上发生了什么大事"). For \
each month given, SEARCH THE WEB for the few great events of that month the whole world spoke of - science, \
technology, space, culture, sport, great public events - the most notable first; real events only, AS REPORTED BY \
MAINSTREAM NEWS ORGANISATIONS (the user: "要求用Mainstream的新闻" - {outlets}), each with the date \
it happened and the address of a page that reports it, nothing made up, nothing about any one private person. Say each \
plainly and fairly, as a good newspaper would, without opinion; a disaster or a war as the facts. \
Answer with only JSON: {{"<month, YYYY-MM>": [{{"date": "<YYYY-MM-DD>", "headline": "<at most 12 words>", "what": \
"<one sentence>", "kind": "<science, technology, space, culture, sport, world>", "url": "<the page's address>"}}, \
...up to {most}], ...}}"""
WORLD_EVENTS_A_MONTH, WORLD_MONTHS_A_CALL, WORLD_EVENT_CALLS_MOST = 5, 6, 2
#: MAINSTREAM NEWS ONLY (the user, 2026-11-19: "要求用Mainstream的新闻"): an event kept only when the page it was reported
#: on is one of these news organisations' (or a part of their site) - anything else let go, however it reads.
MAINSTREAM_NEWS = (
    "reuters.com", "apnews.com", "afp.com", "bbc.com", "bbc.co.uk", "nytimes.com", "washingtonpost.com", "wsj.com",
    "theguardian.com", "ft.com", "bloomberg.com", "economist.com", "cnn.com", "npr.org", "cbsnews.com", "nbcnews.com",
    "abcnews.go.com", "aljazeera.com", "dw.com", "france24.com", "lemonde.fr", "spiegel.de", "nhk.or.jp", "japantimes.co.jp",
    "scmp.com", "xinhuanet.com", "news.cn", "chinadaily.com.cn", "cgtn.com", "people.com.cn", "caixinglobal.com",
    "thepaper.cn", "time.com", "theatlantic.com", "newyorker.com", "usatoday.com", "latimes.com", "politico.com",
    "axios.com", "nature.com", "science.org", "scientificamerican.com", "espn.com", "theverge.com", "wired.com",
    "techcrunch.com", "arstechnica.com", "cnbc.com", "forbes.com", "abc.net.au", "cbc.ca", "straitstimes.com",
)


def mainstream(url: str) -> bool:
    """Whether a page is a mainstream news organisation's (MAINSTREAM_NEWS): its host one of theirs or under one."""
    match = re.match(r"^https?://([^/:?#]+)", str(url or "").strip().lower())
    host = match.group(1) if match else ""
    return any(host == one or host.endswith("." + one) for one in MAINSTREAM_NEWS)


def months_between(first: str, last: str) -> List[str]:
    """Each month from the one of `first` to the one of `last` (dates YYYY-MM-DD), as YYYY-MM."""
    try:
        y, m = int(first[:4]), int(first[5:7])
        y1, m1 = int(last[:4]), int(last[5:7])
    except (TypeError, ValueError):
        return []
    out = []
    while (y, m) <= (y1, m1) and len(out) < 120:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def read_world_events(found: Any, months: List[str]) -> Dict[str, List[Dict[str, str]]]:
    """The events as said, checked: of the months asked only, each with a date in its month and a page's address -
    an event without where it was reported let go; WORLD_EVENTS_A_MONTH a month at most."""
    out: Dict[str, List[Dict[str, str]]] = {}
    if not isinstance(found, dict):
        return out
    short = lambda text, n: " ".join(str(text or "").split())[:n]       # noqa: E731
    for month in months:
        kept = []
        for one in found.get(month) or []:
            if not isinstance(one, dict):
                continue
            date, url, headline = str(one.get("date") or "")[:10], str(one.get("url") or "").strip(), short(one.get("headline"), 120)
            if not (date.startswith(month) and re.match(r"^https?://[^\s]+$", url) and headline and mainstream(url)):
                continue
            kept.append({"date": date, "headline": headline, "what": short(one.get("what"), 300),
                         "kind": short(one.get("kind"), 20), "url": url[:500]})
        out[month] = kept[:WORLD_EVENTS_A_MONTH]
    return out


def news_between(events: Dict[str, Any], first: str, last: str, most: int = 3) -> List[Dict[str, str]]:
    """The world's events from `first` to `last` (dates), the most notable of each month first, spread over the months -
    `most` at most."""
    months = months_between(first, last)
    lists = [[e for e in ((events.get(m) or {}).get("events") or []) if first <= e.get("date", "") <= last + "~"]
             for m in months]
    out: List[Dict[str, str]] = []
    for rank in range(WORLD_EVENTS_A_MONTH):
        for one in lists:
            if rank < len(one) and len(out) < most:
                out.append(one[rank])
    return sorted(out, key=lambda e: e.get("date", ""))


def forest(cards: List[Dict[str, Any]], most: int = TREE_MOST) -> List[Dict[str, Any]]:
    """A task's cards in the order the tree is read, each with its depth: what has no parent among them a root (0).
    At most `most` - the deepest let go first."""
    ids = {one["id"] for one in cards}
    children: Dict[str, List[Dict[str, Any]]] = {}
    roots = []
    for one in cards:
        (children.setdefault(one["parent"], []) if one.get("parent") in ids and one["parent"] != one["id"]
         else roots).append(one)
    out: List[Dict[str, Any]] = []
    seen: set = set()

    def walk(one: Dict[str, Any], depth: int) -> None:
        if one["id"] in seen:
            return
        seen.add(one["id"])
        out.append(dict(one, depth=depth))
        for child in children.get(one["id"], []):
            walk(child, depth + 1)
    for one in roots:
        walk(one, 0)
    if len(out) > most:
        keep = sorted(out, key=lambda c: c["depth"])[:most]
        kept = {c["id"] for c in keep}
        out = [c for c in out if c["id"] in kept]
    return out


def read_trees(found: Any, tasks: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """The trees as the model made them, checked: each card an id, a title, a parent among them (else under the
    root), a status of the four."""
    wanted = {str(t) for t in tasks}
    out: Dict[str, List[Dict[str, Any]]] = {}
    trees = found.get("trees") if isinstance(found, dict) else None
    for task, cards in (trees.items() if isinstance(trees, dict) else []):
        if str(task) not in wanted or not isinstance(cards, list):
            continue
        made = []
        for one in cards[:TREE_MADE_MOST]:
            if not isinstance(one, dict) or not str(one.get("title") or "").strip():
                continue
            made.append({"id": f"{task}:{str(one.get('id') or len(made))[:12]}", "parent": str(one.get("parent") or ""),
                         "title": " ".join(str(one["title"]).split())[:80],
                         "statement": " ".join(str(one.get("statement") or "").split())[:300],
                         "type": str(one.get("type") or "")[:20],
                         "status": str(one.get("status") or "open") if one.get("status") in CARD_STATUSES else "open"})
        ids = {one["id"] for one in made}
        for one in made:
            parent = f"{task}:{one['parent'][:12]}" if one["parent"] else ""
            one["parent"] = parent if parent in ids and parent != one["id"] else ""
        if made:
            out[str(task)] = made
    return out


def subtree(tree: List[Dict[str, Any]], card_id: str) -> List[Dict[str, Any]]:
    """A card and every card under it, in the tree's order."""
    under, out = {card_id}, []
    for one in tree:
        if one["id"] in under or one.get("parent") in under:
            under.add(one["id"])
            out.append(one)
    return out


# -- 2c. the ways between the lands, a layer of their own ---------------------------------------------------------------
# TRANSPORT, DESIGNED LAST AND WHOLE (the user, 2026-11-19: "交通工具也是不同场景有unique的。交通工具有一层专门的设计，包括
# 传送门和不同的交通工具，在layout和各个场景设计完之后才设计，这样可以有更宏伟和联系全局的交通。这个设计让agent follow, overlap
# 比较高的task有更快捷的交通，然后交通工具要符合场景。另外多个互相overlap的场景用火车等交通工具环形连接（魔法门和传送门也算一个
# 交通工具吧）"): once the lands and their places are laid, one call designs every line and every gate between them; the
# code holds it to what can be - a vehicle that suits every land it stops in, what was tied closely joined, lands tied
# all to each other on a ring.
TASK_TRANSPORT = "play_world_transport"
DRY_LANDS_NOT = {"skycity", "underwater", "space"}
#: Each vehicle: what it runs on (rails on the land, water, the sky, the air above all), the lands it suits (their
#: biomes - "*" any it can run in) and how it is drawn (api/web/play-act.js: its engine and its cars, or itself).
VEHICLES: Dict[str, Dict[str, Any]] = {
    "train": {"runs": "rails", "suits": "*", "what": "a steam train on rails - for most lands"},
    "tram": {"runs": "rails", "suits": {"city", "scifi", "toytown", "newyear", "funfair"}, "what": "a city tram"},
    "sleigh": {"runs": "rails", "suits": {"snow", "christmas"}, "what": "a reindeer sleigh on a snow track"},
    "caravan": {"runs": "rails", "suits": {"desert", "primeval", "zoo"}, "what": "a camel caravan on a desert road"},
    "coaster": {"runs": "rails", "suits": {"funfair", "toytown", "valentine"}, "what": "coaster cars on a looping rail"},
    "mine_cart": {"runs": "rails", "suits": {"cavern", "ruins", "primeval", "highland", "graveyard", "halloween"},
                  "what": "mine carts on a narrow track"},
    "ferry": {"runs": "water", "suits": "wet", "what": "a ferry between shores"},
    "sailboat": {"runs": "water", "suits": "wet", "what": "a sailing boat"},
    "airship": {"runs": "sky", "suits": {"skycity", "space", "ordinary"}, "what": "an airship between sky docks"},
    "plane": {"runs": "air", "suits": "*", "what": "a plane over the ground, for the far"},
    "metro": {"runs": "rails", "suits": "*", "what": "an underground metro, quick and many-stopped - a network"},
    "bus": {"runs": "road", "suits": "*", "what": "a bus along the roads"},
    "taxi": {"runs": "road", "suits": {"city", "scifi", "newyear", "toytown", "funfair", "skycity"}, "what": "a taxi"},
    "helicopter": {"runs": "air", "suits": "*", "what": "a helicopter, hopping between near lands"},
    "spaceship": {"runs": "air", "suits": {"space", "scifi", "ordinary"}, "what": "a spaceship - to the stars and back"},
    # Under the sea, in the sky, in space (2026-11-20).
    "submarine": {"runs": "water", "suits": {"underwater", "ordinary"}, "what": "a submarine under the waves"},
    "balloon": {"runs": "sky", "suits": {"skycity", "ordinary"}, "what": "a hot-air balloon drifting between clouds"},
    "rocket": {"runs": "air", "suits": {"space", "scifi", "ordinary"}, "what": "a rocket between worlds"},
    "ufo": {"runs": "air", "suits": {"space", "scifi"}, "what": "a flying saucer, quick and quiet"},
}
#: NO PORTALS (the user, 2026-11-19: "传送门取消，必须是飞机轮船，宇宙飞船，火车，地铁，出租车，公交车，直升机之类"): every way
#: between places is a vehicle. A pair of lands tied this closely has this many lines at least ("联系紧密的project之间交通工具多").
TIE_LINES = ((0.6, 3), (0.3, 2), (0.0, 1))
#: Within one land, places of one project farther apart than this (tiles) have a local line ("project内相隔超过一段距离一定有
#: 一个交通工具").
LOCAL_FAR = 45
#: A portal's look: a column of light, a magic circle, a stone arch, a fairy ring, a teleport pad.
PORTAL_LOOKS = ("light", "ring", "arch", "fairy", "pad")
#: Lands this closely tied are joined, by a line or a gate, whatever was said; three or more tied all together, a ring.
TRANSPORT_TIE = 0.15
# NO LIMIT TO THE WAYS (the user, 2026-11-19: "不要上限" - kept and added to as the world is drawn, a limit would leave
# the lands drawn last only the lines the code fills in).

TRANSPORT_SYSTEM = """You keep a small world that pictures the user's life, its lands laid and their places built. Now \
design THE WAYS BETWEEN THE LANDS as one whole - grand, and tying the world together: LINES OF VEHICLES ONLY - planes, \
ships, spaceships, trains, metros, buses, taxis, helicopters and the rest; no portals, no magic gates. EVERY TWO \
LANDS have at least one line between them; lands whose tasks overlapped most ("ties": how much their days were \
shared) have MORE - a rail network, a metro network running through several of them, a bus besides; lands that all \
overlap one another are joined on a ring; each vehicle suits the lands it runs through - a sleigh in the snow, a \
caravan in the desert, a boat on the water, an airship among the clouds, a spaceship to the stars. \
THE ROADS BETWEEN THE LANDS are part of the same plan (the user: "场景之间的道路和交通工具一样设计，是整体规划"): which \
lands a road joins, what it is made of, how wide (one to three tiles) and how much it winds - a grand avenue between \
lands lived together, a mountain path to a land apart; the roads inside a land are designed with its places. \
THE WAYS GROW WITH THE WORLD: it is drawn a stretch of time at a time, and you are asked again each time its lands \
or their ties change. The ways there already are ("ways_now": lines and roads, each with its id) STAY - answer only \
what to ADD for the lands and ties that are new. Only when the world has changed a lot may you take a few away \
("drop": [their ids]) and design those again among what you add.
HOW EACH TWO LANDS MEET ("joins"; the user, 2026-11-20: "不同主题之间的交通连接和衔接隔断也要model根据场景适配性判断"): \
for each two lands side by side or on one line, how the one passes into the other, as their scenes would - one of \
{joins}: "one_shore" one land running into the next; "strait" water between (a bridge or a boat over it); "void" \
nothing between (the sky's or space's edge - only a vehicle crosses); "clouds" a bank of cloud; "cliff" a cliff edge; \
"wall" a city's wall with its gate; "glass" a glass dome's wall (under the sea, in space). A land in the sky meets the \
ground by clouds, a cliff or the void; one under the sea by the shore, a strait or glass; space by the void or glass.
EVERY VEHICLE TO ITS SCENE (the user: "天空要用天空之船，海底用潜艇，轮船，太空用宇宙飞船"): in the sky an airship or a \
balloon, under the sea a submarine or a ferry, in space a spaceship, a rocket or a flying saucer - each also what \
takes one between its realm and the ground; on the ground trains, metros, buses, planes. A line stops only in lands \
its vehicle suits.
The vehicles: {vehicles}. Road materials: {roads}.
Answer with only JSON: {{"drop": ["<an id of ways_now>", ...or none], "lines": [{{"vehicle": "<one of the vehicles>", "stops": ["<land id>", ...], "ring": true or \
false, "name": "<its name, a few words>", "why": "<at most 2 sentences>"}}, ...], \
"roads": [{{"from": "<land id>", "to": "<land id>", "material": "<one of the materials>", "width": 1 to 3, "curve": 0 \
to 1, "why": "<one sentence>"}}, ...], "joins": [{{"from": "<land id>", "to": "<land id>", "how": "<one of the ways \
lands meet>", "why": "<one sentence>"}}, ...]}}"""


#: EVERY VEHICLE TO ITS SCENE (the user, 2026-11-20: "所有交通工具设置的时候要考虑场景适配性，比如天空要用天空之船，海底用潜艇，
#: 轮船，太空用宇宙飞船等"): the ground's own (trains, metros, buses, planes) only on the ground; each realm - the sky, the
#: sea floor, space - its own, which also takes one down to the ground ("ordinary" among what it suits). Each realm's,
#: the first that may join it to the ground.
REALM_VEHICLES = {"skycity": ("airship", "balloon"), "underwater": ("submarine", "ferry"), "space": ("spaceship", "rocket")}


def land_is(land: Dict[str, Any], suits: Any) -> bool:
    """Whether a vehicle that suits these may stop in this land: "*" the ground only (not the sky, the sea floor or
    space); "wet" by the water; a set its lands - "ordinary" in it, any land of the ground too."""
    wet = "sea" in (land.get("edges") or []) or land.get("layout") == "islands" or land.get("biome") in {"seaside", "lakeside", "underwater"}
    biome = land.get("biome")
    if suits == "*any":
        return True
    if suits == "*":
        return biome not in DRY_LANDS_NOT
    if suits == "wet":
        return wet
    return biome in suits or ("ordinary" in suits and biome not in DRY_LANDS_NOT)


# -- 2d. the way from one map to the next ---------------------------------------------------------------------------
# A MAP KEPT TO A SIZE, THE NEXT ONE REACHED BY A WAY DESIGNED FOR IT (the user, 2026-11-20: "一个地图差不多我现在的量多一点
# 之后就要设计next 图（是下一个小地图），这样防止一个图过大。然后出现这个的时候，要有一个transition设计stage, 让模型设计一个美观的，
# 酷炫的，意想不到的到其它地图的入口（在另一个地图也有对应的入口）"): a map holds playregions.AREA_REGIONS lands' cells; the next
# land begins a new map. Then one call designs the pair of entrances between them - each end drawn as a great piece is
# (playpieces), the page sets each by its land's heart and goes through from one to the other.
TASK_CROSSING = "play_world_crossing"
#: An end's size in tiles, wide and high (its picture rises above it as a great piece's does).
CROSSING_W, CROSSING_H = (3, 6), (2, 5)

CROSSING_SYSTEM = """You keep a small world that pictures the user's life. It grew too large for one map, so it goes on in a NEW MAP: "map_b" begins where "map_a" was full. Design THE WAY BETWEEN THE TWO MAPS: a PAIR OF ENTRANCES, one standing in each map, that belong together - going in at one, the walker comes out of the other. Make it BEAUTIFUL, STRIKING and UNEXPECTED - what nobody would think of first, yet right for both maps' scenes and for what the user's days there were (each land's "places"). For instance a whale whose open mouth is a harbour on one shore and whose spout lands the walker on the other; a giant beanstalk's lift; a library shelf that swings round into an observatory - invent your own, never one of these. NOT a plain glowing ring, an arch of light or a door of mist (the user wants no generic portals): something one goes into, climbs or rides through. EACH END FITS ITS OWN MAP - its land's scene and colours - and THE TWO ANSWER EACH OTHER: two halves of one thing, its outside and its inside, one creature at both ends. For each end choose the land of its map it stands in (the two maps' lands most tied - "ties" - if any) and its size in tiles: w {w0} to {w1}, h {h0} to {h1}.
Answer with only JSON: {{"name": "<the way's name, a few words>", "why": "<at most 2 sentences: why this way between these two maps>", "ends": [{{"map": "a", "land": "<a land id of map_a>", "name": "<this end's name, a few words>", "what": "<what it looks like, for its painter: its shape, its colours, what is striking about it - 2 to 4 sentences>", "going": "<what one sees and feels going through it, one sentence>", "w": {w0} to {w1}, "h": {h0} to {h1}}}, {{"map": "b", "land": "<a land id of map_b>", "name": "...", "what": "...", "going": "...", "w": ..., "h": ...}}]}}"""


def crossing_system() -> str:
    return CROSSING_SYSTEM.format(w0=CROSSING_W[0], w1=CROSSING_W[1], h0=CROSSING_H[0], h1=CROSSING_H[1])


def read_crossing(found: Any, lands_a: List[Dict[str, Any]], lands_b: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """THE WAY AS DESIGNED, KEPT TO WHAT CAN BE (the code's): one end in each map, each in a land of its own map (else
    the map's first), its size within CROSSING_W x CROSSING_H, its words short. None when there is no pair."""
    if not isinstance(found, dict) or not lands_a or not lands_b:
        return None
    short = lambda text, n: " ".join(str(text or "").split())[:n]       # noqa: E731
    ends = [one for one in found.get("ends") or [] if isinstance(one, dict)]
    out = []
    for side, lands in (("a", lands_a), ("b", lands_b)):
        one = next((e for e in ends if str(e.get("map") or "").lower() == side), None)
        if one is None:
            one = ends[len(out)] if len(ends) > len(out) else None
        if one is None or not short(one.get("what"), 10):
            return None
        ids = [str(land["id"]) for land in lands]
        land = str(one.get("land") or "")

        def size(value: Any, bounds: Tuple[int, int]) -> int:
            try:
                return max(bounds[0], min(bounds[1], int(round(float(value)))))
            except (TypeError, ValueError):
                return bounds[0] + 1
        out.append({"map": side, "land": land if land in ids else ids[0], "name": short(one.get("name"), 60) or "",
                    "what": short(one.get("what"), 700), "going": short(one.get("going"), 240),
                    "w": size(one.get("w"), CROSSING_W), "h": size(one.get("h"), CROSSING_H)})
    return {"name": short(found.get("name"), 60), "why": short(found.get("why"), 400), "ends": out}


# -- THE WORLD'S LOOKS AS A WHOLE, AFTER ITS WAYS (the user, 2026-11-20: "在交通规划之后加一个stage, 是整体美学规划 ... i) 看一下
# 地块衔接段的设计，根据地形性质选择合适且美观的图 ii) 做夜景的设置，看各个灯光的位置让整体夜景好看"; "路沿 ... 让agent决定要加什么
# 路沿，弄好形状之后代码加") -------------------------------------------------------------------------------------------------
TASK_AESTHETICS = "play_world_aesthetics"
#: What may edge a road, what may stand beside it now and then, and how a land's night may be lit.
ROAD_EDGES = ("none", "stones", "hedge", "flowers", "posts")
STREET_THINGS = ("bench", "bin", "lamp", "planter", "hydrant", "signpost")
NIGHT_LIGHTS = ("lamp", "gaslamp", "torch", "brazier")
AESTHETICS_SYSTEM = """You are the art director of a small world that pictures the user's life - its lands, its \
places and its ways laid. Now design how it LOOKS AS A WHOLE, three things:
1. WHERE TWO LANDS MEET ("joins"): for each pair given, how the one passes into the other as their grounds would - one \
of {joins} - and how wide the passing is ("width": 1 to 8 tiles). Beautiful and fitting: a meadow into a forest by \
one shore, the sky over the ground by clouds or a cliff, the sea floor behind glass.
2. THE ROADS' EDGES ("roads"): for each kind of road (its material) what edges it - one of {edges} - and what stands \
beside it now and then ("things": up to 3 of {things}; "every": 3 to 14 tiles between them): a gravel path edged with \
stones, a bench and a bin now and then; a paved street with lamps and hydrants; a forest track with nothing but \
stones. Wide roads more, narrow paths less.
3. THE NIGHT ("night"): for each land, its lights - "light": one of {lights}; "colour": "#rrggbb" (warm gold in a \
village, cool white in a city, sea-green under the sea, violet in space); "brightness": 0.2 to 1; "reach": 2 to 9 \
tiles; "windows": 0 to 1 (how many windows glow); "lamps_every": 4 to 16 tiles of road between street lights - so \
the whole night reads well: pools of light along the ways, the places glowing, dark between.
Answer with only JSON: {{"joins": [{{"from": "<land id>", "to": "<land id>", "how": "<one of the ways>", "width": <1-8>, \
"why": "<one sentence>"}}, ...], "roads": [{{"material": "<a road material given>", "edge": "<one of the edges>", \
"things": ["<...>"], "every": <3-14>}}, ...], "night": [{{"land": "<land id>", "light": "<one of the lights>", \
"colour": "#rrggbb", "brightness": <0.2-1>, "reach": <2-9>, "windows": <0-1>, "lamps_every": <4-16>}}, ...]}}"""


def aesthetics_system() -> str:
    return AESTHETICS_SYSTEM.format(joins=", ".join(JOINS), edges=", ".join(ROAD_EDGES), things=", ".join(STREET_THINGS),
                                    lights=", ".join(NIGHT_LIGHTS))


def read_aesthetics(found: Any, lands: List[Dict[str, Any]], materials: List[str]) -> Dict[str, Any]:
    """The art director's design, kept to what can be: joins of lands there are, roads of materials there are, nights
    of lands there are - every number in its range."""
    found = found if isinstance(found, dict) else {}
    ids = {str(one["id"]) for one in lands}

    def number(value: Any, least: float, most: float, fallback: float) -> float:
        try:
            return max(least, min(most, float(value)))
        except (TypeError, ValueError):
            return fallback
    colour = lambda value, fallback: value if re.fullmatch(r"#[0-9a-fA-F]{6}", str(value or "")) else fallback   # noqa: E731
    joins = []
    for one in found.get("joins") or []:
        if isinstance(one, dict) and str(one.get("from")) in ids and str(one.get("to")) in ids and one.get("from") != one.get("to") \
                and one.get("how") in JOINS:
            joins.append({"from": str(one["from"]), "to": str(one["to"]), "how": one["how"],
                          "width": int(number(one.get("width"), 1, 8, 3)), "why": " ".join(str(one.get("why") or "").split())[:200]})
    roads = {}
    for one in found.get("roads") or []:
        if isinstance(one, dict) and str(one.get("material")) in materials:
            roads[str(one["material"])] = {"edge": one.get("edge") if one.get("edge") in ROAD_EDGES else "none",
                                           "things": [t for t in (one.get("things") or []) if t in STREET_THINGS][:3],
                                           "every": int(number(one.get("every"), 3, 14, 8))}
    night = {}
    for one in found.get("night") or []:
        if isinstance(one, dict) and str(one.get("land")) in ids:
            night[str(one["land"])] = {"light": one.get("light") if one.get("light") in NIGHT_LIGHTS else "lamp",
                                       "colour": colour(one.get("colour"), "#ffbe6e"),
                                       "brightness": round(number(one.get("brightness"), 0.2, 1, 0.6), 2),
                                       "reach": round(number(one.get("reach"), 2, 9, 5), 1),
                                       "windows": round(number(one.get("windows"), 0, 1, 0.5), 2),
                                       "lamps_every": int(number(one.get("lamps_every"), 4, 16, 9))}
    return {"joins": joins, "roads": roads, "night": night}


#: How two lands may meet (TRANSPORT_SYSTEM "joins"; play-regions.js lays each).
JOINS = ("one_shore", "strait", "void", "clouds", "cliff", "wall", "glass")


def transport_system() -> str:
    return TRANSPORT_SYSTEM.format(
        vehicles="; ".join(f"{k} ({v['what']})" for k, v in VEHICLES.items()),
        roads=", ".join(ROAD_MATERIALS), joins=", ".join(JOINS))


def ways_now(now: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """The ways there are, as the design is shown them: each line and road with its id (L1.., R1..)."""
    now = now if isinstance(now, dict) else {}
    return {"lines": [{"id": f"L{n + 1}", **{k: one.get(k) for k in ("vehicle", "stops", "ring", "name")}}
                      for n, one in enumerate(now.get("lines") or [])],
            "roads": [{"id": f"R{n + 1}", **{k: one.get(k) for k in ("from", "to", "material", "width")}}
                      for n, one in enumerate(now.get("roads") or [])]}


def read_transport(found: Any, lands: List[Dict[str, Any]], ties: List[Dict[str, Any]],
                   now: Optional[Dict[str, Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """THE WAYS AS DESIGNED, KEPT TO WHAT CAN BE (the code's guarantee): the ways there were (`now`) kept but those
    the design dropped, each still stopping in two lands there are; then each line added a vehicle that suits every
    land it stops in, two stops or more, a ring of three or more. Then what the design left out: three or more lands
    tied all to one another and on no ring together, a ring of the vehicle that suits them all; a pair tied closely
    given more lines; every two lands joined by something."""
    by_id = {str(one["id"]): one for one in lands}
    out: Dict[str, List[Dict[str, Any]]] = {"lines": [], "portals": [], "roads": [], "joins": []}   # "portals" kept empty
    found = found if isinstance(found, dict) else {}
    short = lambda text, n: " ".join(str(text or "").split())[:n]       # noqa: E731
    dropped = {str(one) for one in found.get("drop") or [] if isinstance(one, (str, int))}
    for n, one in enumerate((now or {}).get("lines") or []):      # THE WAYS THERE WERE, BUT THOSE DROPPED
        stops = [s for s in one.get("stops") or [] if s in by_id]
        if f"L{n + 1}" not in dropped and len(stops) >= 2:
            out["lines"].append({**one, "stops": stops, "ring": bool(one.get("ring")) and len(stops) >= 3})
    for n, one in enumerate((now or {}).get("roads") or []):
        if f"R{n + 1}" not in dropped and one.get("from") in by_id and one.get("to") in by_id:
            out["roads"].append(dict(one))
    for one in found.get("lines") or []:
        if not isinstance(one, dict) or one.get("vehicle") not in VEHICLES:
            continue
        stops = [s for s in dict.fromkeys(str(s) for s in one.get("stops") or []) if s in by_id]
        if len(stops) < 2 or not all(land_is(by_id[s], VEHICLES[one["vehicle"]]["suits"]) for s in stops):
            continue
        out["lines"].append({"vehicle": one["vehicle"], "stops": stops, "ring": bool(one.get("ring")) and len(stops) >= 3,
                             "name": short(one.get("name"), 40), "why": short(one.get("why"), 240)})

    # HOW EACH TWO LANDS MEET, AS THE MODEL JUDGED THEIR SCENES (2026-11-20): kept from before unless said again.
    for one in list(found.get("joins") or []) + list((now or {}).get("joins") or []):
        if not isinstance(one, dict):
            continue
        a, b, how = str(one.get("from") or ""), str(one.get("to") or ""), str(one.get("how") or "")
        if a in by_id and b in by_id and a != b and how in JOINS and \
                not any({j["from"], j["to"]} == {a, b} for j in out["joins"]):
            out["joins"].append({"from": a, "to": b, "how": how, "why": short(one.get("why"), 200)})
    for one in found.get("roads") or []:                         # the roads between lands, as planned
        if not isinstance(one, dict):
            continue
        a, b, way = str(one.get("from") or ""), str(one.get("to") or ""), road_of(one)
        if a in by_id and b in by_id and a != b and way and not any({r["from"], r["to"]} == {a, b} for r in out["roads"]):
            out["roads"].append({"from": a, "to": b, **way, "why": short(one.get("why"), 200)})

    def joined(a: str, b: str) -> int:
        return sum(1 for line in out["lines"] if a in line["stops"] and b in line["stops"])

    def suiting(stops: List[str], order: Tuple[str, ...], not_these: set = frozenset()) -> Optional[str]:
        return next((k for k in order if k not in not_these and all(land_is(by_id[s], VEHICLES[k]["suits"]) for s in stops)),
                    None)
    close = [(str(t["from"]), str(t["to"])) for t in ties if float(t.get("tie") or 0) >= TRANSPORT_TIE
             and str(t["from"]) in by_id and str(t["to"]) in by_id]
    tied = {frozenset(pair) for pair in close}
    # three or more all tied to one another: a ring (first - then a gate for any pair still joined by nothing)
    ids = sorted({x for pair in close for x in pair})
    groups: List[List[str]] = []
    for a in ids:
        for group in groups:
            if all(frozenset((a, b)) in tied for b in group):
                group.append(a)
                break
        else:
            groups.append([a])
    for group in [g for g in groups if len(g) >= 3]:
        if any(line["ring"] and set(group) <= set(line["stops"]) for line in out["lines"]):
            continue
        vehicle = suiting(group, ("metro", "train", "tram", "bus")) or "plane"
        out["lines"].append({"vehicle": vehicle, "stops": group, "ring": True, "name": "", "why": "These days all overlap."})
    # CLOSE TIES, MORE WAYS (TIE_LINES): a pair tied this closely given lines till it has as many - each of another kind.
    for one in ties:
        a, b = str(one.get("from") or ""), str(one.get("to") or "")
        if a not in by_id or b not in by_id or a == b:
            continue
        wanted = next(n for least, n in TIE_LINES if float(one.get("tie") or 0) >= least)
        while joined(a, b) < wanted:
            used = {line["vehicle"] for line in out["lines"] if a in line["stops"] and b in line["stops"]}
            vehicle = suiting([a, b], ("metro", "train", "bus", "tram", "ferry", "helicopter", "plane"), used)
            if vehicle is None:
                break
            out["lines"].append({"vehicle": vehicle, "stops": [a, b], "ring": False, "name": "",
                                 "why": "Your days were shared between these."})
    # EVERY TWO LANDS A WAY (the user, 2026-11-19: "每两个大project(区域）之间至少有一个交通工具"): those no line joins, on a
    # network that runs through them all - rails where they can be, else in the air.
    ids = list(by_id)
    apart = sorted({x for i, a in enumerate(ids) for b in ids[i + 1:] if not joined(a, b) for x in (a, b)})
    if apart:
        ground = [s for s in apart if land_is(by_id[s], VEHICLES["train"]["suits"])]
        if len(ground) >= 2:
            out["lines"].append({"vehicle": "train", "stops": ground, "ring": len(ground) >= 3, "name": "",
                                 "why": "Every land reached."})
        left = sorted({x for i, a in enumerate(ids) for b in ids[i + 1:] if not joined(a, b) for x in (a, b)})
        if left:
            vehicle = suiting(left, ("plane", "helicopter", "airship", "spaceship", "submarine"))
            if vehicle:
                out["lines"].append({"vehicle": vehicle, "stops": left, "ring": len(left) >= 3, "name": "",
                                     "why": "Every land reached."})
            else:
                # NONE GOES TO THEM ALL (a land in the sky, one under the sea): each land of a realm joined to the ground
                # by its realm's own (REALM_VEHICLES), the ground's by plane.
                ground_ids = [s for s in ids if by_id[s].get("biome") not in DRY_LANDS_NOT]
                for s in [x for x in left if by_id[x].get("biome") in REALM_VEHICLES]:
                    to = next((g for g in ground_ids if g != s), None)
                    others = [x for x in ids if x != s]
                    pair = [s, to] if to else [s, others[0]] if others else []
                    own = suiting(pair, REALM_VEHICLES[by_id[s]["biome"]]) if pair else None
                    if own:
                        out["lines"].append({"vehicle": own, "stops": pair, "ring": False, "name": "",
                                             "why": "Every land reached, each by its own."})
                flat = [x for x in left if x in ground_ids]
                if len(flat) >= 2:
                    out["lines"].append({"vehicle": "plane", "stops": flat, "ring": len(flat) >= 3, "name": "",
                                         "why": "Every land reached."})
    return out


def local_lines(zones: List[Dict[str, Any]], land: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A PROJECT'S FAR PLACES JOINED (LOCAL_FAR): within one land, the places of one project farther apart than that - a
    line through them, nearest to nearest, of the vehicle that suits the land (a tram in a city, a sleigh in the snow,
    else a bus). `zones` one project's places in `land`."""
    centre = lambda z: (z["x"] + z["w"] / 2, z["y"] + z["h"] / 2)       # noqa: E731
    if len(zones) < 2:
        return []
    far = max(math.dist(centre(a), centre(b)) for a in zones for b in zones)
    if far <= LOCAL_FAR:
        return []
    biome = str(land.get("biome") or "")
    vehicle = "tram" if biome in VEHICLES["tram"]["suits"] else "sleigh" if biome in VEHICLES["sleigh"]["suits"] \
        else "caravan" if biome in VEHICLES["caravan"]["suits"] else "submarine" if biome == "underwater" else \
        "balloon" if biome == "skycity" else "ufo" if biome == "space" else "bus"
    order = [zones[0]]
    rest = zones[1:]
    while rest:
        nxt = min(rest, key=lambda z: math.dist(centre(order[-1]), centre(z)))
        order.append(nxt)
        rest.remove(nxt)
    return [{"vehicle": vehicle, "places": [z["id"] for z in order], "land": land["id"], "ring": False}]


# -- 3. each important room, designed on its own ---------------------------------------------------------------------
ROOM_SYSTEM = """You are the set designer of one room in a small world that pictures the user's life - a room the \
user will walk into. Design it the way the warm, crowded rooms of an old 2D adventure game are furnished: every \
thing at its own size, and the room made full and alive by LAYERS - on the wall, hanging from the ceiling, set on \
the tables and shelves, standing on the floor, lying under foot - with ways to walk between. Let it come from its \
muses ("muses": a poem, a piece of music, a painting, a video, and what they ask) and from the project's idea \
("concept"); let one look - its colours, its light - hold it together, \
and let a few things be the ones the eye goes to.
ITS WORK ("cards": the cards of the user's work this room holds - a tree: each its id, title, what is true when it \
is done, its status, how deep it sits): EVERY CARD IS SHOWN BY AT LEAST ONE THING - its entry says "card": "<the \
card's id>" - and a card with parts by its parts round it. Show what it is, not its name: a release a crate being \
packed, a test a lab bench, a design a drafting table; done, it stands finished; working, half made; open, waiting \
its turn; set aside, covered and put by.
ITS WAY THROUGH, AS IT WAS LIVED (the user, 2026-11-20: "跟着箭头/路牌走能够带user重走user在时序上对这些project的经历"; \
"包括一个项目内部发生的progress"): lay the cards' things along ONE WALKING WAY from the way in ("D"), in the order the \
work happened ("first": the day each was first worked on) - the earliest nearest the door, what is being worked on \
now at its end - with floor kept free between each and the next. Arrows on the floor and a numbered sign by each \
are laid along it for them; they walk the work again, step by step.
THE ROOM: "grid" - its floor, row by row from the top: "." floor, "#" a wall inside it, "D" the way in (kept clear), \
"X" what stands there and stays. Its back wall above the floor is "wall_width" tiles across ("wall_taken": the \
columns where something already hangs).
AN OPEN PLACE ("open_air": true - a garden, a square, a field, a yard): no walls and nothing on one; the same care \
- its things in scenes with paths between them, what grows and what stands, its work shown \
card by card as in a room.
THEIR OWN WORK, ON SHOW - AS CARTOON THINGS (the user, 2026-11-19: "要让agent知道可以加入你自己的成果之类的信息"; "这个 \
地图上应该画的是卡通物品，然后交互细节里有图片（比如申请表应该是个相关的物品）"): never a screenshot on the map - each piece \
of their work shown by the thing that is it (an application form a clipboard with a form, a paper a stack of pages, \
a talk a lectern); a frame, a poster, a banner or a statue is drawn as one. "their_pictures" (each its id, what it \
shows, when) go IN THE CARD of the thing that stands for them - "picture": "<its id>"; never one twice. Or BUILD ONE \
AFTER ITS PICTURE as a great piece, in the world's pixel style ("pieces", its "picture": the id).
A PICTURE IN EACH THING'S CARD (the user, 2026-11-19: "程序不一定要看已有截图，而是要去找自己觉得合适的"; "模型主要rely on \
自己搜，只是用户截图也是搜的一个source"): any thing - the lamp for an invitation, the desk for a paper - may ask for one, \
"find_picture": "<THE THING ITSELF, as exactly as you can say it - their own work (their paper, their code, their \
release, their draft, their data) said to be theirs and named as its card names it; a public thing (a journal, a \
product, a place, an event) by its public name - never an example or a template of something like it>" (the user, \
2026-11-20: "这个材料包的照片明显不是我的"). It is drawn as itself; the picture is LOOKED FOR - in their own files, \
screenshots and pages for what is theirs, on the web for what is public - and kept in its card for when they look \
closely. Ask WHEREVER YOU THINK A \
PICTURE FITS (the user: "觉得可以配图的就配图"). On the map only cartoon things; the real pictures are in the cards. What they achieved - a card \
done, a result, a release - is shown as an achievement, proudly: its picture framed, a trophy, a certificate.
THE WORLD THEN ("the_world_then": what happened in the world while this work was done): show it as ONE thing of the \
room, the way such a room would hold the news - a newspaper on the table, a radio, a television, a notice board, a \
poster - its entry saying "news": true.
PROGRESS WIDGETS - things that change as the work goes on, from their first stage to their last, by themselves \
(the code shows the stage the card has reached): {widgets}. Give one "card": <the card> (or, on a great ground, \
"stage": <its n>). MILESTONES, A STAGE'S RESULT, ANYTHING SPECIAL ARE BEST SHOWN BY A WIDGET (or a great piece) - \
choose among them what fits the work.
ADDING ("adding": true): the room is already furnished - what is there ("there") stays as the "X" on its floor; add \
only the things for these cards, round what is there, in the room's own idea ("idea"), leaving ways to walk.
Answer with only JSON: {{"idea": "<the moment someone walking in should feel, at most 2 sentences>",
 "floor_material": "<its floor's material>", "wall_material": "<its walls' material, under a roof>", "make": [{{"kind": "thing", \
"floor" or "wall", "name": "...", "description": "...", "w": 1, "h": 1}}, ...only what the library has not],
 "legend": {{"<a letter A-Z or a-z>": {{"kind": "<a kind of thing>", "name": "<short>", "why": "<at most 2 \
sentences, to them>", "card": "<the card it stands for, if one>", "news": true (the one that shows the world then), \
"picture": "<one of their_pictures' ids, for its card>", "find_picture": "<the thing itself its card's picture \
should show - theirs said to be theirs, a public one by its name, never an example>", "task_note": "<how it ties to \
their work, one short sentence>", "fun_fact": "<a fun fact about the thing or its subject, one or two sentences, no \
links>"}}, ...}} - "picture", "find_picture", "task_note" and "fun_fact" each only where it adds something (the user, \
2026-11-20: "每个做好的item可以描述一下相关的task, 和fun fact ... 每个不需要全加满"),
 "floor": ["<the grid again, row by row, each thing's letter on every tile it covers (a 2 x 2 bed is 2 rows of 2 of \
its letter); '.' '#' 'D' 'X' kept as they were>", ...],
 "under": [{{"kind": "<rug, paint>", "x": <column>, "y": <row>, "name": "...", "why": "..."}}],
 "wall": [{{"kind": "<a thing for a wall: {wall_kinds}>", "x": <column of the wall>, "name": "...", "why": "...", \
"picture": "<a picture's id, for its card>"}}],
 "on": [{{"kind": "<a kind of the list>", "on": "<the letter of the table, desk, shelf or counter it is set on>", \
"name": "...", "why": "..."}}],
 "hang": [{{"kind": "<a kind of the list that would hang there - those made to hang among them: chandelier, \
hanging_plant, wind_chime, birdcage, mobile, hanging_cloth, crystal_chandelier, pumpkin_lantern, bunting, lantern, \
lanternString; and EACH THEME'S OWN (choose those of the room's own theme and land): {hangings}>", "x": <column of its left>, "y": <row of its top>, \
"name": "...", "why": "..."}}]}}
EVERY KIND THERE IS, FOR YOU TO CHOOSE (by size, columns x rows - anything here may stand, be set on a table or \
hang): choose what this room and its work ask for, many kinds, not one kind over and over: {kinds}."""
ROOM_LOOK_SYSTEM = """You designed this room of a small world that pictures the user's life (your design is given) - \
here it is drawn. Look at it as its set designer would, once: what is wrong - a corner left bare, a way blocked, the \
same thing too often, colours that fight, too sparse or too cluttered, its muses not felt, the eye with nowhere to \
go. Then give the room again with that put right, in the same JSON as before, and "looked": "<what you changed, at \
most 2 sentences>"."""
#: How many rooms are designed in one growth (each two calls: the design, the look) - all that wait, as the user chose
#: (2026-11-19: "一次全部做完"); this many at most, against a runaway.
ROOMS_MOST = 60
#: A ROOM'S CARDS A FEW AT A TIME (the user, 2026-11-19: "每个设计一次性只能输入N个卡片 ... 可以多次输入"; "N稍微小一点，
#: 不需要80个"): this many cards to a design; each answer at most this long; a room whose designs all failed tried
#: this many times at most, then left to the things the code gives its cards.
CARDS_A_CALL, ROOM_ANSWER_MOST, ROOM_TRIES_MOST = 12, 8000, 2
#: A designed room's new cards stand in as plain things till this many have gathered, then it is designed again for
#: them (the user, 2026-11-19: "新卡攒够了要分批加入再设计").
NEW_CARDS_LEAST = 4
#: A room's things are designed only in a room this large (smaller ones are filled as before).
ROOM_LEAST_TILES = 24


def room_grid(floor: set, walls: set, clear: set, kept: set) -> Tuple[List[str], int, int]:
    """The room as the model is shown it: rows from its top, "." floor, "#" wall, "D" the way in, "X" kept."""
    xs, ys = [x for x, _y in floor | walls], [y for _x, y in floor | walls]
    left, top = min(xs), min(ys)
    rows = []
    for y in range(top, max(ys) + 1):
        row = ""
        for x in range(left, max(xs) + 1):
            at = (x, y)
            row += "#" if at in walls or at not in floor else "X" if at in kept else "D" if at in clear else "."
        rows.append(row)
    return rows, left, top


def read_room(said: Any, rows: List[str], left: int, top: int, sizes: Callable[[str], Optional[Tuple[int, int]]],
              wall_kinds: set, wall_width: int, wall_taken: Optional[set] = None) -> Dict[str, List[Dict[str, Any]]]:
    """THE ROOM AS DESIGNED, KEPT TO WHAT CAN BE (the code's guarantee, not the model's): each thing at its own size,
    on tiles all its letter, all floor, none kept clear or kept; a thing on another on a thing that is there; what
    hangs and what lies under foot on the floor; what is on the wall a thing for a wall, along the wall."""
    out: Dict[str, List[Dict[str, Any]]] = {"floor": [], "under": [], "wall": [], "on": [], "hang": []}
    if not isinstance(said, dict):
        return out
    legend = {str(k)[:1]: v for k, v in (said.get("legend") or {}).items()
              if isinstance(v, dict) and str(k)[:1].isalpha() and sizes(str(v.get("kind") or ""))}
    drawn = [str(r) for r in (said.get("floor") or [])][:len(rows)]
    free = {(x, y) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch == "."}
    claimed: Dict[Tuple[int, int], str] = {}
    hosts: Dict[str, List[Dict[str, Any]]] = {}

    def words(one: Dict[str, Any]) -> Dict[str, str]:
        said = {"name": " ".join(str(one.get("name") or "").split())[:60],
                "why": " ".join(str(one.get("why") or "").split())[:400]}
        if str(one.get("card") or "").strip():
            said["card"] = str(one["card"]).strip()[:80]
        if one.get("news") is True:
            said["news"] = True
        if isinstance(one.get("stage"), int) and 0 < one["stage"] <= STAGES_MOST:
            said["stage"] = one["stage"]                 # a great ground's stage it shows
        if str(one.get("picture") or "").strip():
            said["picture"] = str(one["picture"]).strip()[:120]   # one of their pictures, hung (playworld _lay_room)
        found = one.get("find_picture")
        found = " ".join(str((found.get("search") or found.get("what") or "") if isinstance(found, dict) else found or "")
                         .split())[:200]
        if found:
            said["find_picture"] = found                 # looked for, on the web first (agent/playfind.py)
        # ITS CARD'S OWN LINES (2026-11-20): how it ties to their work, a fun fact - no links.
        for key, most in (("task_note", 240), ("fun_fact", 400)):
            text = re.sub(r"https?://\S+|www\.\S+", "", " ".join(str(one.get(key) or "").split())).strip()[:most]
            if text:
                said[key] = text
        return said
    for y, row in enumerate(drawn):
        for x, ch in enumerate(row):
            if ch not in legend or (x, y) in claimed:
                continue
            kind = str(legend[ch]["kind"])
            w, h = sizes(kind) or (1, 1)
            tiles = {(x + dx, y + dy) for dx in range(w) for dy in range(h)}
            if tiles <= free and all(y + dy < len(drawn) and x + dx < len(drawn[y + dy]) and drawn[y + dy][x + dx] == ch
                                     for dx in range(w) for dy in range(h)) and not tiles & set(claimed):
                for t in tiles:
                    claimed[t] = ch
                thing = {"kind": kind, "x": left + x, "y": top + y, "letter": ch, **words(legend[ch])}
                out["floor"].append(thing)
                hosts.setdefault(ch, []).append(thing)

    def at(one: Any, wall: bool = False) -> Optional[Tuple[int, int]]:
        try:
            x = int(one.get("x"))
            y = 0 if wall else int(one.get("y"))
        except (TypeError, ValueError, AttributeError):
            return None
        return x, y
    for one in said.get("under") or []:
        spot, size = at(one), sizes(str((one or {}).get("kind") or "")) if isinstance(one, dict) else None
        if spot and size and str(one["kind"]) in ("rug", "paint") and \
                {(spot[0] + dx, spot[1] + dy) for dx in range(size[0]) for dy in range(size[1])} <= free:
            out["under"].append({"kind": str(one["kind"]), "x": left + spot[0], "y": top + spot[1], **words(one)})
    taken_wall: set = set(wall_taken or ())            # what already hangs there
    for one in said.get("wall") or []:
        spot = at(one, wall=True) if isinstance(one, dict) else None
        kind = str((one or {}).get("kind") or "") if isinstance(one, dict) else ""
        size = sizes(kind)
        if spot and size and kind in wall_kinds and 0 <= spot[0] and spot[0] + size[0] <= wall_width and \
                not set(range(spot[0], spot[0] + size[0])) & taken_wall:
            taken_wall |= set(range(spot[0], spot[0] + size[0]))
            out["wall"].append({"kind": kind, "x": left + spot[0], **words(one)})
    # SET ON A TABLE, HUNG FROM ABOVE: any kind the room may hold (up to 2 x 5, `sizes`), not one small kind (the user,
    # 2026-11-19: "所有内饰只要不超过2*5的大小都是可以的") - on its host side by side as wide as the host, its foot on it;
    # hanging over the floor, every tile of it.
    used: Dict[int, int] = {}
    for one in said.get("on") or []:
        size = sizes(str(one.get("kind") or "")) if isinstance(one, dict) else None
        if not size:
            continue
        for host in hosts.get(str(one.get("on") or "")[:1], []):
            n = used.get(id(host), 0)
            if n + size[0] <= max(1, (sizes(host["kind"]) or (1, 1))[0]):
                used[id(host)] = n + size[0]
                out["on"].append({"kind": str(one["kind"]), "x": host["x"] + n, "y": host["y"] - (size[1] - 1),
                                  "on": host, **words(one)})
                break
    for one in said.get("hang") or []:
        spot = at(one) if isinstance(one, dict) else None
        size = sizes(str(one.get("kind") or "")) if spot else None
        if size and all((spot[0] + dx, spot[1] + dy) in free or (spot[0] + dx, spot[1] + dy) in claimed
                        for dx in range(size[0]) for dy in range(size[1])):
            out["hang"].append({"kind": str(one["kind"]), "x": left + spot[0], "y": top + spot[1], "hang": True,
                                **words(one)})
    return out


# -- 4. the room drawn, for the model to look at ---------------------------------------------------------------------
_SPRITES: Optional[Dict[str, List[Any]]] = None
_SPEC = re.compile(r'\b(\w+): \["(\w+)", (\d+), (\d+), (\d+), (\d+)')


def sprites() -> Dict[str, List[Any]]:
    """Each kind's picture on its sheet, as the page draws it (play.js PLAY_ITEMS and its first look, the scenery of
    play-regions.js, the catalogue): {kind: [sheet, column, row, width, height]}."""
    global _SPRITES
    if _SPRITES is None:
        found: Dict[str, List[Any]] = {}
        try:
            from . import playcatalogue
            for name, one in playcatalogue.catalogue()["things"].items():
                found[name] = list(one["spec"][:5])
        except Exception:
            pass
        for page in ("play-regions.js", "play.js"):           # the page's own last: as it is drawn
            try:
                text = (WEB / page).read_text(encoding="utf-8")
            except OSError:
                continue
            for name, sheet, col, row, w, h in _SPEC.findall(text):
                found[name] = [sheet, int(col), int(row), int(w), int(h)]
        _SPRITES = found
    return _SPRITES


def draw_room(rows: List[str], things: List[Dict[str, Any]], left: int, top: int, wall_width: int,
              sizes: Callable[[str], Optional[Tuple[int, int]]], tile: int = 32) -> Optional[Dict[str, str]]:
    """THE ROOM AS A PICTURE ({"media_type", "data"}): its back wall, its floor, what lies, stands, is set on and hangs -
    each from its own sheet; a thing with no picture its outline and its name. None when it cannot be drawn."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    width, height = max(len(r) for r in rows), len(rows)
    out = Image.new("RGBA", (width * tile, (height + 2) * tile), (40, 36, 44, 255))
    draw = ImageDraw.Draw(out)
    draw.rectangle([0, 0, wall_width * tile, 2 * tile - 1], fill=(150, 120, 96, 255))           # the back wall
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            fill = (60, 52, 60, 255) if ch == "#" else (196, 168, 128, 255) if (x + y) % 2 else (188, 160, 122, 255)
            draw.rectangle([x * tile, (y + 2) * tile, (x + 1) * tile - 1, (y + 3) * tile - 1], fill=fill)
    sheets: Dict[str, Any] = {}

    def picture(kind: str, w: int, h: int):
        spec = sprites().get(kind)
        if not spec:
            return None
        try:
            sheet = sheets.get(spec[0]) or Image.open(PLAY / f"{spec[0]}.png").convert("RGBA")
        except OSError:
            return None
        sheets[spec[0]] = sheet
        piece = Image.new("RGBA", (spec[3] * 16, spec[4] * 16), (0, 0, 0, 0))
        for ty in range(spec[4]):
            for tx in range(spec[3]):
                piece.paste(sheet.crop(((spec[1] + tx) * 17, (spec[2] + ty) * 17,
                                        (spec[1] + tx) * 17 + 16, (spec[2] + ty) * 17 + 16)), (tx * 16, ty * 16))
        return piece.resize((w * tile, h * tile), Image.NEAREST)
    order = {"under": 0, "floor": 1, "wall": 1, "on": 2, "hang": 3}
    for one in sorted(things, key=lambda t: (order.get(t.get("layer", "floor"), 1), t.get("y", 0))):
        w, h = sizes(one["kind"]) or (1, 1)
        px = (one["x"] - left) * tile
        py = tile * (0 if one.get("layer") == "wall" else (one["y"] - top + 2))
        if one.get("layer") == "on":
            py -= tile // 3
        if one.get("layer") == "hang":
            draw.line([px + tile // 2, 2 * tile, px + tile // 2, py], fill=(30, 30, 30, 255))
            py -= tile // 2
        shown = picture(one["kind"], w, h)
        box = [px + 2, max(0, py) + 2, px + w * tile - 3, max(0, py) + h * tile - 3]
        if shown is not None:
            out.alpha_composite(shown, (px, max(0, py)))
        elif one["kind"] in ("rug", "paint"):                   # drawn by the page itself: a rug, a patch of paint
            draw.rectangle(box, fill=(150, 52, 48, 255), outline=(222, 184, 92, 255), width=2)
        elif one.get("layer") == "wall":                        # a painting, a frame: a framed picture
            draw.rectangle(box, fill=(92, 128, 156, 255), outline=(120, 82, 40, 255), width=3)
        else:
            draw.rectangle([px + 1, py + 1, px + w * tile - 2, py + h * tile - 2], outline=(250, 250, 250, 255))
            draw.text((px + 3, py + 3), one["kind"][:8], fill=(250, 250, 250, 255))
    buffer = io.BytesIO()
    out.convert("RGB").save(buffer, "PNG")
    return {"media_type": "image/png", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}


def json_of(text: str) -> Dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        return json.loads(match.group(0)) if match else {}
    except ValueError:
        return {}


__all__ = ["featured_steps", "stages_of", "TASK_WORLD_EVENTS", "WORLD_EVENTS_SYSTEM", "months_between",
           "read_world_events", "news_between", "MOOD_SCENES", "SCENE_THEMES", "theme_of", "scenes_open", "feelings_between", "mood_system", "read_mood", "scene_settings", "MUSE_SYSTEM", "read_muses",
           "ROOM_SYSTEM", "ROOM_LOOK_SYSTEM", "room_grid", "read_room", "draw_room", "sprites", "json_of",
           "TASK_MOOD", "TASK_MUSE", "TASK_ROOM", "TASK_ROOM_LOOK", "ROOMS_MOST", "MOOD_LAND_DAYS"]

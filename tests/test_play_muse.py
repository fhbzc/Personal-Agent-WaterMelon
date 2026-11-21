"""The world's feeling, its muses and its rooms (agent/playmuse.py; the user, 2026-11-18: "我感觉你现在还是创意不够";
"让agent用诗歌，音乐，绘画，视频各找一个符合她意境的内容 ... 不要条条框框"; "比如我某段时间悲伤，就是雪天"; "场景要和天气搭配";
"室内的东西的size就正常 ... 你只能靠加布置"). Everything here is made up."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401

from personalized_agent.agent import playmuse as muse
from personalized_agent.agent import playplaces as places
from personalized_agent.agent import playregions as regions
from personalized_agent.agent import playworld as pw

CONFIG = SimpleNamespace(get=lambda key, default=None: default)


def two_steps(said):
    """The feeling's two calls answered: its paragraph (play_world_mood), then its land from it (play_world_mood_scene)."""
    return {muse.TASK_MOOD: {"mood": said.get("mood") or "These days were long and quiet."}, muse.TASK_MOOD_SCENE: said}


class ByTask:
    """A model answering each of the growth's calls by its name."""

    def __init__(self, said):
        self.said, self.asked = said, []

    def invoke(self, request, role=""):
        self.asked.append(request)
        return SimpleNamespace(text=json.dumps(self.said.get(request.task, {})))


class Rows:
    """A database of a few rows, asked by what its query reads."""

    def __init__(self, messages, states):
        self.messages, self.states = messages, states

    def query(self, sql, args):
        return self.messages if "FROM messages" in sql else self.states if "FROM states" in sql else []


class TheFeelingChoosesTheSceneTests(unittest.TestCase):
    def test_every_scene_is_a_land_and_its_weather_goes_with_it(self) -> None:
        for name, one in muse.MOOD_SCENES.items():
            self.assertIn(one["biome"], regions.BIOMES, name)
            self.assertIn(one["weather"], regions.WEATHERS, name)
            self.assertIn(one["palette"], regions.PALETTES, name)
            self.assertIn(one["layout"], regions.LAYOUTS, name)
            if one.get("nature"):
                self.assertIn(one["nature"], pw.ZONE_KINDS, name)
        # "场景要和天气搭配，比如雪山就是雪天": a snowy land snows.
        for name in ("snowfield", "snowy_peaks"):
            self.assertEqual(muse.MOOD_SCENES[name]["weather"], "snowy")

    def test_read_from_their_words_and_the_rhythm_of_their_days_only(self) -> None:
        db = Rows([{"created_at": "2026-11-03T14:00:00+00:00", "content": "So tired of this bug"}],
                  [{"created_at": "2026-11-03T09:00:00"}, {"created_at": "2026-11-03T23:30:00"},
                   {"created_at": "2026-11-04T10:00:00"}])
        read = muse.feelings_between(db, "2026-11-01", "2026-11-08")
        self.assertEqual(set(read), {"their_words", "their_rhythm"})
        self.assertIn("So tired of this bug", read["their_words"][0])
        self.assertEqual(len(read["their_rhythm"]), 2, "one line a day")
        self.assertTrue(any("moments at work" in line for line in read["their_rhythm"]))

    def test_a_paragraph_first_then_the_land_from_the_paragraph_alone(self) -> None:
        """2026-11-19: "agent看了这个心情之后写的一段话，然后根据这段话再选地区，而不是直接生成心情词（一段话表达内容更多一点）"."""
        with tempfile.TemporaryDirectory() as folder:
            said = two_steps({"mood": "Slow rainy days, deep in one thing, a little tired by night.", "scene": "rainy_forest",
                              "land_name": "The Dripping Wood", "why": "w"})
            keeper = pw.PlayWorld(Path(folder), ByTask(said), CONFIG,
                                  feelings=lambda s, u: {"their_words": ["one more bug"], "their_rhythm": ["d"]})
            mood = keeper._mood(keeper.world(), "2026-11-01", "2026-11-08")
            first, second = keeper.models.asked
            self.assertEqual((first.task, second.task), (muse.TASK_MOOD, muse.TASK_MOOD_SCENE))
            self.assertIn("their_words", first.messages[0]["content"])
            told = json.loads(second.messages[0]["content"])
            self.assertEqual(told, {"mood": "Slow rainy days, deep in one thing, a little tired by night."},
                             "the land chosen from the paragraph alone")
            self.assertNotIn("feeling", mood, "no mood word")
            self.assertEqual(mood["mood"], told["mood"])
        from personalized_agent.common import modules
        self.assertEqual(modules.module_of(muse.TASK_MOOD_SCENE), "play_world")

    def test_a_scene_not_on_the_list_is_no_feeling(self) -> None:
        self.assertIsNone(muse.read_mood({"scene": "lava_moon"}))
        mood = muse.read_mood({"feeling": "low", "scene": "snowfield", "land_name": "The Quiet Drifts",
                               "why": "You wrote less, and late.", "changed": True})
        self.assertEqual((mood["scene"], mood["changed"]), ("snowfield", True))

    def test_a_new_feeling_opens_a_land_of_its_scene_and_weather_whatever_was_said(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            said = two_steps({"feeling": "low", "scene": "snowy_peaks", "land_name": "The Long Climb",
                                     "why": "Long days, few words.", "changed": True})
            keeper = pw.PlayWorld(Path(folder), ByTask(said), CONFIG, feelings=lambda s, u: {"their_words": ["ok"]})
            world = keeper.world()
            mood = keeper._mood(world, "2026-11-01T00:00:00", "2026-11-08T00:00:00")
            self.assertEqual(world["moods"][-1]["scene"], "snowy_peaks", "kept, each stretch its own")
            self.assertFalse(keeper._mood_opens_land(world, mood, "2026-11-08"), "the starting village left by its own rule")
            world["regions"][-1].update(starter=False, made="2026-10-20", biome="seaside", scene="sunny_islands")
            self.assertTrue(keeper._mood_opens_land(world, mood, "2026-11-08"))
            self.assertFalse(keeper._mood_opens_land(world, {**mood, "changed": False}, "2026-11-08"), "a small swing")
            self.assertFalse(keeper._mood_opens_land(world, mood, "2026-10-20"), "a land stands its day first")
            keeper._mood_now, keeper._mood_opens = mood, True
            region = keeper._open_region(world, {"biome": "desert", "weather": "fair", "theme": "quiet",
                                                 "architecture": "stone", "landmark": "fountain"}, "2026-11-08", None)
            self.assertEqual((region["biome"], region["weather"], region["scene"]), ("snow", "snowy", "snowy_peaks"))
            self.assertEqual(region["name"], "The Long Climb", "named by the feeling when the growth named none")
            self.assertTrue(any(z["kind"] == "peak" and z["region"] == region["id"] for z in world["zones"]),
                            "its mountain stands in it")
            self.assertEqual(world["regions"][-2]["scene"], "sunny_islands", "the land before keeps its look")


class AHouseFirstTests(unittest.TestCase):
    """2026-11-19: "不要从起始村，从一个起始房子，是看不到外面的那种"; "一开始数据不足的时候，先装饰home, 不要画外景，等有足够数据
    （心情）再设置初始外景，第一次走出"; "海岛什么的也是根据心情进行的想象 ... 要给几十种不同的景色主题"."""

    GROWN = {"content_switch": {"switched": True, "what": "bees"},
             "zones": [{"kind": "study", "name": "Hive census", "why": "Bees."}],
             "items": [{"zone": "Hive census", "kind": "desk", "name": "Counts", "why": "w", "from": "f"}]}

    def test_a_new_world_is_a_house_alone_and_what_grows_goes_in_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({pw.TASK: self.GROWN}), CONFIG)
            world = keeper.world()
            self.assertTrue(world["regions"][0]["closed"], "no world round the house yet")
            home = world["zones"][0]["id"]
            keeper.apply(dict(self.GROWN), [], "2026-11-19T10:00:00+00:00")
            world = keeper.world()
            self.assertEqual([z["kind"] for z in world["zones"]], ["home"], "no place made outside")
            self.assertEqual({one["zone"] for one in world["items"]}, {home}, "every thing in the house")
            self.assertIn("Counts", [one["name"] for one in world["items"]])

    def test_a_feeling_read_from_enough_opens_the_world_outside_as_its_land(self) -> None:
        self.assertGreaterEqual(len(muse.MOOD_SCENES), 40, "dozens of lands to imagine from")
        self.assertIn('"mood": "<the paragraph>"', muse.MOOD_SYSTEM, "the feeling written first")
        with tempfile.TemporaryDirectory() as folder:
            said = two_steps({"mood": "Long quiet days.", "feeling": "calm", "scene": "misty_lake",
                                     "land_name": "Stillwater", "why": "You wrote slowly."})
            few = pw.PlayWorld(Path(folder) / "a", ByTask(said), CONFIG, feelings=lambda s, u: {"their_words": ["hi"]})
            world = few.world()
            mood = few._mood(world, "2026-11-01", "2026-11-08")
            self.assertLess(mood["evidence"], muse.FIRST_LAND_EVIDENCE, "too little to know them by")
            many = pw.PlayWorld(Path(folder) / "b", ByTask(said), CONFIG,
                                feelings=lambda s, u: {"their_words": ["w"] * 6, "their_rhythm": ["d"] * 4})
            world = many.world()
            mood = many._mood(world, "2026-11-01", "2026-11-08")
            self.assertEqual(mood["mood"], "Long quiet days.")
            many._open_outside(world, mood, "2026-11-08")
            region = world["regions"][0]
            self.assertNotIn("closed", region)
            self.assertEqual((region["biome"], region["weather"], region["layout"], region["name"]),
                             ("lakeside", "foggy", "river", "Stillwater"))
            self.assertFalse(region["starter"])
            self.assertTrue(any(z["kind"] == "lake" for z in world["zones"]), "its lake by the house")
            self.assertEqual(world["outside_opened"], "2026-11-08")

    def test_the_lands_music_chosen_by_the_feeling(self) -> None:
        """2026-11-19: "你那个音乐也根据心情选"."""
        self.assertIn('"music": the land', muse.mood_scene_system("home (warm)", "piano", "solo"))
        with tempfile.TemporaryDirectory() as folder:
            said = two_steps({"mood": "m", "feeling": "low", "scene": "snowfield", "land_name": "Drifts",
                                     "why": "w", "music": {"tempo": 60, "instrument": "piano", "mode": "minor",
                                                           "melody": "A4 C5 E5 - D5 C5 B4 A4", "bass": "A2 F2"}})
            keeper = pw.PlayWorld(Path(folder), ByTask(said), CONFIG,
                                  feelings=lambda s, u: {"their_words": ["w"] * 9})
            world = keeper.world()
            mood = keeper._mood(world, "2026-11-01", "2026-11-08")
            self.assertEqual((mood["music"]["tempo"], mood["music"]["mode"]), (60, "minor"))
            keeper._open_outside(world, mood, "2026-11-08")
            self.assertEqual(world["regions"][0]["music"], mood["music"], "the land plays it")
        # 2026-11-19: "其实不应该有公共区域，所有位置的音乐就是他最近的主题区域的音乐".
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn("function playNearestZone(x, y, within = Infinity) {", page)
        # 2026-11-19: "户外也可以有自己的音乐" - near a place its music, farther out the land's own.
        self.assertIn("return playNearestZone(x, y, PLAY_PLACE_HEARD) || playRegionSound(region) || playNearestZone(x, y);", page)
        self.assertEqual(page.count("function playHeard("), 1, "the music's chooser no longer hides the things' sounds")
        sound = (support.ROOT / "personalized_agent" / "api" / "web" / "play-sound.js").read_text(encoding="utf-8")
        # 2026-11-19: each of the scenes its own music, each kind of place its own - none falling back to the town's.
        for scene in muse.MOOD_SCENES:
            self.assertIn(f"\n  {scene}: {{ tempo:", sound, scene)
        for kind in pw.ZONE_KINDS:
            self.assertRegex(sound, rf"\n  {kind}: \{{ tempo:", kind)
        for melody in re.findall(r'(?:melody|bass): "([^"]+)"', sound):
            for note in melody.split():
                self.assertRegex(note, r"^(-|[A-G][#b]?[1-7])$", melody)
        self.assertIn("const fallback = scene ? PLAY_SCENE_TUNES[scene] : PLAY_TUNES[kind] || PLAY_TUNES.town;", sound)
        self.assertIn("if (heard && heard.id !== play.heardId) { play.heardId = heard.id; playSoundRoom(heard); }", page)
        self.assertNotIn("if (!zone) playSoundRoom(playRegionSound(region));", page, "no common ground's music")

    def test_only_a_wet_land_has_the_sea_round_it(self) -> None:
        """2026-11-19: "我看你现在出来是海岛，好像之前都是海岛" - a forest the feeling chose had been drawn an island."""
        web = support.ROOT / "personalized_agent" / "api" / "web"
        regions_js = (web / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn('const PLAY_WET_BIOMES = new Set(["seaside", "lakeside", "underwater"]);', regions_js)
        self.assertIn("} else if (!playWetLand(regions[a])) {", regions_js)
        self.assertIn("&& !islands && playWetLand(regions[a])) {", regions_js, "no beach round a dry land")
        # 2026-11-19: "这里好像有个隐形的墙，走不过去": the edge's ground walked; only what grows there, and the dark, in the way.
        self.assertNotIn("if (rim[i]) solid[i] = 1;", regions_js)
        # 2026-11-19: "你要防止未来出现这个情况呀": a tile stands in the way only where it shows it - whatever made it solid.
        self.assertIn("if (!solid[i] || seen[i] || playSolidSeen(ground[i])) continue;", regions_js)
        # "你设计该挡路的地方，你就种上挡路的东西": planted with what stands there in its scene - opened only where nothing can.
        self.assertIn("const name = region && playStandingOf(region, x, y);", regions_js)
        self.assertIn("} else solid[i] = 0;", regions_js)
        # the edge grown shut: its own rule before the one that keeps growth off what is solid
        self.assertLess(regions_js.index("    if (rim[i]) {"), regions_js.index("    if (near(x, y, (j) => kept[j] === 1"))
        self.assertIn("if (!underfoot && playTileDrawn(spec, dx, dy)) { solid[i] = 1; seen[i] = 1; }", regions_js,
                      "a picture's transparent corners never walls")
        self.assertIn("if (made[1] && i1 >= 0) seen[i1] = 1;", regions_js, "a mountain's rock its own")

    def test_the_page_lays_the_house_alone_and_names_the_land_at_the_first_step_out(self) -> None:
        web = support.ROOT / "personalized_agent" / "api" / "web"
        regions_js = (web / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("const closed = regions.length === 1 && Boolean(regions[0].closed);", regions_js)
        self.assertIn("if (index.closed) {", regions_js)
        page = (web / "play.js").read_text(encoding="utf-8")
        self.assertIn("function playFirstOutside(region)", page)
        self.assertIn('playWords("The world outside, for the first time")', page)


class TheMusesTests(unittest.TestCase):
    def test_what_is_found_is_shown_only_as_the_public_may_and_what_is_made_whole(self) -> None:
        found = {"muses": [{"project": "Hive Census",
                            "poem": {"title": "Bees at Dusk", "by": "A Poet", "lines": ["a found line"],
                                     "url": "https://example.org/poem", "public_domain": False},
                            "music": {"title": "Hum", "by": "your agent",
                                      "tune": {"tempo": 90, "instrument": pw.INSTRUMENTS[0],
                                               "accompaniment": pw.INSTRUMENTS[0], "style": pw.ARRANGEMENTS[0],
                                               "mode": "minor", "melody": "C5 D5 E5 - G5 E5 D5 C5", "bass": "C3 G2 A2 F2"}},
                            "painting": {"title": "Comb", "by": "your agent",
                                         "pixels": {"palette": ["#ffcc00", "#000000"], "rows": ["01" * 16] * 32}},
                            "video": {"title": "A hive", "url": "not an address"},
                            "design": "Gold light, wax and quiet.", "colours": ["#ffcc00", "nope"]},
                           {"project": "Someone else's", "design": "x"}]}
        read = muse.read_muses(found, ["Hive Census"], pw.tune_of, pw.drawn_pixels)
        self.assertEqual(list(read), ["Hive Census"], "only the projects asked of")
        one = read["Hive Census"]
        self.assertEqual(one["poem"]["lines"], [], "a found poem not the public's: its link, never its words")
        self.assertEqual(one["poem"]["url"], "https://example.org/poem")
        self.assertIn("tune", one["music"])
        self.assertIn("pixels", one["painting"])
        self.assertNotIn("video", one, "no address, no video")
        self.assertEqual(one["colours"], ["#ffcc00"])


class EachRoomDesignedTests(unittest.TestCase):
    ROOM = {"idea": "A warm hive of a study.",
            "legend": {"B": {"kind": "bed", "name": "Rest", "why": "For the long nights."},
                       "T": {"kind": "table", "name": "Census table", "why": "Where the counts are laid."},
                       "S": {"kind": "bookshelf", "name": "Field notes", "why": "Every season's notes."}},
            "floor": ["........BB.S", "........BB.S", "............", "..TT........", "............",
                      "............"],
            "under": [{"kind": "rug", "x": 4, "y": 4, "name": "Honey rug", "why": "Gold underfoot."}],
            "wall": [{"kind": "window", "x": 3, "name": "East light", "why": "Morning on the counts."},
                     {"kind": "bed", "x": 6, "name": "Not for a wall", "why": "-"}],
            "on": [{"kind": "candle", "on": "T", "name": "Candle", "why": "Late counts."}],
            "hang": [{"kind": "lantern", "x": 3, "y": 2, "name": "Lantern", "why": "Over the table."}]}

    def _keeper(self, folder, said):
        keeper = pw.PlayWorld(Path(folder), ByTask(said), CONFIG)
        world = keeper.world()
        world["zones"][0]["designed"] = "before"          # the home's own design is another test's
        zone = keeper._add_zone(world, "study", "Hive census", "Counting bees.", "2026-11-08")
        zone.update(task="t1", w=12, h=8)
        world["tasks"] = {"t1": {"minutes": 9000, "fill": 0.6}}
        world["projects"] = [{"id": "p1", "name": "Hive Census", "tasks": ["t1"], "summary": ""}]
        world["muses"] = {"p1": {"poem": {"title": "Bees at Dusk", "by": "your agent", "lines": ["Gold in the comb"]},
                                 "painting": {"title": "Comb", "pixels": pw.drawn_pixels(
                                     {"palette": ["#ffcc00"], "rows": ["0" * 32] * 32}, 32, 32)},
                                 "design": "Gold light."}}
        keeper.save(world)
        return keeper, zone["id"]

    def test_a_room_is_laid_in_layers_at_its_things_own_sizes_and_kept_so(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper, zone_id = self._keeper(folder, {muse.TASK_ROOM: self.ROOM, muse.TASK_ROOM_LOOK: {}})
            self.assertEqual(keeper._design_rooms("2026-11-08"), 1)
            world = keeper.world()
            zone = next(z for z in world["zones"] if z["id"] == zone_id)
            self.assertEqual(zone["designed"], "2026-11-08")
            self.assertEqual(zone["muse"]["poem"]["lines"], ["Gold in the comb"], "its muses on its card")
            mine = [one for one in world["items"] if one["zone"] == zone_id]
            kinds = {one["kind"] for one in mine}
            self.assertTrue({"bed", "table", "bookshelf", "rug", "window", "candle", "lantern"} <= kinds)
            self.assertTrue(any(one.get("muse") and one["kind"] == "pixel_art_large" for one in mine), "its painting")
            self.assertTrue(any(one.get("poem") for one in mine), "its poem on a post")
            self.assertFalse(any(one.get("scale") for one in mine), "nothing made large in a room")
            table = next(one for one in mine if one["kind"] == "table")
            self.assertEqual(next(one for one in mine if one["kind"] == "candle")["on"], table["id"], "set on the table")
            self.assertTrue(next(one for one in mine if one["kind"] == "lantern")["hang"])
            self.assertEqual(sum(1 for one in mine if one["kind"] == "bed"), 1, "a bed for a wall is no wall's")
            self.assertEqual(len({one["id"] for one in world["items"]}), len(world["items"]), "no id twice")
            self.assertEqual(keeper._thicken(world, "2026-11-09"), 0, "a room designed whole is not filled with rows")
            # The model asked once to design it and once, shown it drawn, to look at it.
            tasks = [one.task for one in keeper.models.asked]
            self.assertEqual(tasks, [muse.TASK_ROOM, muse.TASK_ROOM_LOOK])
            looked = keeper.models.asked[1].messages[0]["content"]
            self.assertEqual([part["type"] for part in looked], ["text", "image"])

    def test_what_it_is_shown_after_is_laid_when_it_is_better(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            again = {**self.ROOM, "legend": {"P": {"kind": "piano", "name": "Piano", "why": "Its tune."}},
                     "floor": ["............"] * 3 + ["PP.........."] + ["............"] * 2, "looked": "A piano, for its music."}
            keeper, zone_id = self._keeper(folder, {muse.TASK_ROOM: self.ROOM, muse.TASK_ROOM_LOOK: again})
            keeper._design_rooms("2026-11-08")
            world = keeper.world()
            zone = next(z for z in world["zones"] if z["id"] == zone_id)
            self.assertIn("piano", {one["kind"] for one in world["items"] if one["zone"] == zone_id})
            self.assertEqual(zone["room_looked"], "A piano, for its music.")

    def test_a_places_card_shows_no_muses_and_no_summary_of_the_agents(self) -> None:
        # 2026-11-20: "这里这串英文是什么。 另外你那些诗,音乐之类的素材不要显示，这些放在后台就行".
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertNotIn("    ${playMuseCard(zone)}\n", page)
        self.assertNotIn('${zone.task_summary ? `<p class="side-play-card-from chat-message">${escapeHtml(zone.task_summary)}</p>` : ""}', page)
        self.assertIn("task_title", pw.PlayWorld._words_of({"task_title": "Keep up a chat", "name": "x"}))

    def test_the_maps_pictures_kept_beside_the_world(self) -> None:
        # 2026-11-20: "小地图为什么不是一个存好后面都不update呢，小地图除非是修改了画的内容，不然都不变呀".
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({}), CONFIG)
            self.assertEqual(keeper.minimap(), {"v": "", "blocks": {}})
            self.assertEqual(keeper.keep_minimap("6", [["0,0", "s1", "data:image/png;base64,AAA"],
                                                       ["0,1", "s2", "not a picture"]]), 1)
            self.assertEqual(keeper.keep_minimap("6", [["1,0", "s3", "data:image/png;base64,BBB"]]), 1)
            said = keeper.minimap()
            self.assertEqual(said["v"], "6")
            self.assertEqual(sorted(said["blocks"]), ["0,0", "1,0"], "added to, the bad one left out")
            keeper.keep_minimap("7", [["2,2", "s4", "data:image/png;base64,CCC"]])
            self.assertEqual(sorted(keeper.minimap()["blocks"]), ["2,2"], "another drawing: the old let go")
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn('api.get("/api/play/minimap")', page)
        self.assertIn('api.post("/api/play/minimap", { v: playMiniKeptVersion(), blocks, ...(whole ? { whole } : {}) })', page)
        self.assertIn("mine.sigs = new Map(todo.map(([cx, cy]) => [`${cx},${cy}`, playMiniSig(index, cx, cy)]));", page)
        self.assertIn("if (!index.miniOwn) {", page, "the world sorted into blocks once, not looked through for each")
        self.assertNotIn("typeof playLayBlock === \"function\" ? playLayBlock", page, "no code hash in the version")
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('@route("GET", "/api/play/minimap")', routes)
        self.assertIn('@route("POST", "/api/play/minimap")', routes)

    def test_a_room_the_same_when_the_world_is_drawn_again_takes_its_design_with_no_call(self) -> None:
        # 2026-11-20: "重画不从零开始 ... 那你改吧".
        with tempfile.TemporaryDirectory() as folder:
            keeper, zone_id = self._keeper(folder, {muse.TASK_ROOM: self.ROOM, muse.TASK_ROOM_LOOK: {}})
            before = keeper.world()
            keeper._design_rooms("2026-11-08")
            self.assertIn(muse.TASK_ROOM, [one.task for one in keeper.models.asked])
            self.assertTrue((Path(folder) / pw.ROOM_DESIGNS_FILE).exists())
            kinds = sorted(one["kind"] for one in keeper.world()["items"] if one["zone"] == zone_id)
            keeper.save(before)                          # drawn again: the room as it was before it was designed
            keeper.models.asked.clear()
            keeper._design_rooms("2026-11-08")
            self.assertEqual([one.task for one in keeper.models.asked
                              if one.task in (muse.TASK_ROOM, muse.TASK_ROOM_LOOK)], [], "no call: its design used again")
            self.assertEqual(sorted(one["kind"] for one in keeper.world()["items"] if one["zone"] == zone_id), kinds)

    def test_the_rooms_instructions_the_same_each_time_for_the_cache(self) -> None:
        # 2026-11-20: cache hits - the library told with each room, the look on the design's own instructions.
        with tempfile.TemporaryDirectory() as folder:
            keeper, _zone_id = self._keeper(folder, {muse.TASK_ROOM: self.ROOM, muse.TASK_ROOM_LOOK: {}})
            keeper._design_rooms("2026-11-08")
            room, look = keeper.models.asked[0], keeper.models.asked[1]
            self.assertEqual(room.system, look.system, "the design and its look share one cache")
            self.assertNotIn("none yet", room.system.split("THE LIBRARY")[0] if "THE LIBRARY" in room.system else "")
            self.assertIn("library_now", json.loads(room.messages[0]["content"]))
            self.assertIn("now_look_at_it", json.loads(look.messages[0]["content"][0]["text"]))
        source = Path(pw.__file__).read_text(encoding="utf-8")
        self.assertIn("chosen = sorted(rooms[:muse.ROOMS_MOST], key=lambda z: not indoors(z))", source)

    def test_a_place_of_no_task_is_designed_too(self) -> None:
        """2026-11-19: "你查一下为什么" - four rooms of a stretch before any task were left undesigned."""
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({muse.TASK_ROOM: self.ROOM, muse.TASK_ROOM_LOOK: {}}), CONFIG)
            world = keeper.world()
            world["zones"][0]["designed"] = "before"
            library = keeper._add_zone(world, "library", "Reading room", "Long reads.", "2026-09-01")
            library.update(w=12, h=8)
            keeper.save(world)
            self.assertEqual(keeper._design_rooms("2026-09-14"), 1)
            sent = json.loads(keeper.models.asked[0].messages[0]["content"])
            self.assertNotIn("cards", sent, "no work: laid in layers all the same")
            self.assertTrue(next(z for z in keeper.world()["zones"] if z["id"] == library["id"])["designed"])

    def test_the_door_way_and_what_is_kept_stay_clear(self) -> None:
        rows = ["....", "..X.", ".DD."]
        laid = muse.read_room({"legend": {"A": {"kind": "chair"}}, "floor": ["AAAA", "AAAA", "AAAA"]}, rows, 10, 20,
                              pw.PlayWorld._room_size, set(), 4)
        spots = {(one["x"] - 10, one["y"] - 20) for one in laid["floor"]}
        self.assertEqual(spots, {(0, 0), (1, 0), (2, 0), (3, 0), (0, 1), (1, 1), (3, 1), (0, 2), (3, 2)})

    def test_every_kind_up_to_two_by_five_is_offered_and_may_hang_or_stand_on_a_table(self) -> None:
        # 2026-11-19: "房间设计要给他所有内饰的种类，让他自己选，另外所有内饰只要不超过2*5的大小都是可以的"
        kinds = pw.PlayWorld._room_kinds(pw.PlayWorld.__new__(pw.PlayWorld))
        for kind in ("lantern", "lamp", "bed", "bookshelf"):
            self.assertIn(f" {kind},", kinds.replace(":", ",") + ",")
        self.assertNotIn("fountain", kinds, "3 x 3: larger than a room's thing")
        self.assertIsNone(pw.PlayWorld._room_fit("fountain"))
        # "只对室内生效": an open place keeps its great things.
        self.assertIn("fountain", pw.PlayWorld._room_kinds(pw.PlayWorld.__new__(pw.PlayWorld), True))
        self.assertEqual(pw.PlayWorld._room_fit("fountain", True), (3, 3))
        self.assertEqual(pw.PlayWorld._room_fit("lamp"), (1, 2))
        self.assertNotIn("a lantern, a lamp, a garland", muse.ROOM_SYSTEM)
        rows = ["....", "....", ".DD."]
        laid = muse.read_room({"legend": {"T": {"kind": "table"}}, "floor": ["TT..", "TT..", "...."],
                               "on": [{"kind": "lamp", "on": "T"}], "hang": [{"kind": "lamp", "x": 3, "y": 0}]},
                              rows, 0, 0, pw.PlayWorld._room_fit, set(), 4)
        size = pw.PlayWorld._room_fit("table")
        if size and size[0] >= 1:
            self.assertEqual([one["kind"] for one in laid["on"]], ["lamp"], "a 1 x 2 thing set on a table")
        self.assertEqual([(one["kind"], one["x"], one["y"]) for one in laid["hang"]], [("lamp", 3, 0)],
                         "a 1 x 2 thing hung, over free floor")

    def test_a_room_is_drawn_for_the_model(self) -> None:
        rows = ["......", "......", "..DD.."]
        picture = muse.draw_room(rows, [{"kind": "bed", "x": 0, "y": 0, "layer": "floor"},
                                        {"kind": "window", "x": 2, "y": 0, "layer": "wall"},
                                        {"kind": "no_such_kind", "x": 4, "y": 0, "layer": "floor"}],
                                 0, 0, 6, lambda kind: (2, 2) if kind == "bed" else (1, 1))
        self.assertEqual(picture["media_type"], "image/png")
        self.assertGreater(len(picture["data"]), 200)
        self.assertIn("bed", muse.sprites(), "the page's own pictures")


class EveryCardOfTheWorkAThingTests(unittest.TestCase):
    """2026-11-19: "你把每个project 都变成work tree, 然后起码每个card都要有一个（或者多个item)代表"; "这个工作树可以保留，允许
    复用（对每个树的task, 自己生成)"."""

    CARDS = [{"id": "c0", "parent": "", "title": "Hive census", "statement": "Bees counted", "type": "", "status": "working"},
             {"id": "c1", "parent": "c0", "title": "Count sheet", "statement": "A sheet per hive", "type": "data", "status": "done"},
             {"id": "c2", "parent": "c0", "title": "Field report", "statement": "Written up", "type": "document", "status": "open"},
             {"id": "c3", "parent": "c2", "title": "Charts", "statement": "Three charts", "type": "design", "status": "set_aside"}]

    def test_the_work_panels_cards_read_as_a_tree(self) -> None:
        class Db:
            sqls: list = []

            def query(self, sql, args):
                self.sqls.append(sql)
                if "experience_stages" in sql:         # what each card took: two tries, one left open for days
                    return [{"card_id": "k2", "kind": "execution", "began": "2026-10-01T10:00:00+00:00",
                             "ended": "2026-10-01T11:30:00+00:00", "deliverable": "A first part", "summary": ""},
                            {"card_id": "k2", "kind": "adjustment", "began": "2026-10-02T10:00:00+00:00",
                             "ended": "2026-10-05T10:00:00+00:00", "deliverable": "The part, put right", "summary": ""}]
                return [{"id": "k1", "unit_id": "t1", "task_id": "t1", "parent_id": "", "type": "", "title": "Goal",
                         "statement": "", "status": "open"},
                        {"id": "k2", "unit_id": "", "task_id": "t1", "parent_id": "k1", "type": "code", "title": "Part",
                         "statement": "Works", "status": "done"}]
        db = Db()
        read = muse.work_cards(db, ["t1"])
        self.assertFalse([s for s in db.sqls if "UPDATE" in s.upper()], "read only")
        part = read["t1"][1]
        # 2026-11-19: "featured step (特别难的，花了特别多时间的步骤)" - each stage counted at most STAGE_HOURS_MOST hours.
        self.assertEqual((part["minutes"], part["tries"], part["reworks"], part["first"]),
                         (90 + 60 * muse.STAGE_HOURS_MOST, 2, 1, "2026-10-01"))
        self.assertEqual(part["result"], "The part, put right")
        self.assertNotIn("minutes", read["t1"][0], "no work on it: nothing said")
        tree = muse.forest(read["t1"])
        self.assertEqual([(c["id"], c["depth"]) for c in tree], [("k1", 0), ("k2", 1)])
        self.assertEqual([c["id"] for c in muse.subtree(muse.forest(self.CARDS), "c2")], ["c2", "c3"])

    def test_a_task_without_a_tree_given_one_once_and_kept(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            said = {muse.TASK_TREE: {"trees": {"t2": [{"id": "c1", "parent": "", "title": "Garden", "status": "working"},
                                                      {"id": "c2", "parent": "c1", "title": "Beds dug", "status": "done"},
                                                      {"id": "c3", "parent": "nowhere", "title": "Seeds", "status": "odd"}]}}}
            keeper = pw.PlayWorld(Path(folder), ByTask(said), CONFIG, cards=lambda ids: {"t1": list(self.CARDS)})
            world = keeper.world()
            trees = keeper._trees_of(world, ["t1", "t2"], "2026-11-19")
            self.assertEqual([c["id"] for c in trees["t1"]], ["c0", "c1", "c2", "c3"], "the Work panel's own")
            self.assertEqual([c["depth"] for c in trees["t2"]], [0, 1, 0], "a parent not there: a root of its own")
            self.assertEqual(trees["t2"][2]["status"], "open")
            asked = json.loads(keeper.models.asked[0].messages[0]["content"])
            self.assertEqual([one["id"] for one in asked["tasks"]], ["t2"], "only the task with none")
            keeper.models.asked.clear()
            keeper._trees_of(world, ["t1", "t2"], "2026-11-20")
            self.assertEqual(keeper.models.asked, [], "kept, and used again")
            # 2026-11-19: "我说的复用只要是play world 能复用就行": beside the world, so a world drawn again keeps it.
            self.assertNotIn("trees", world)
            keeper.path.rename(keeper.path.with_name("world.before-test.json"))
            keeper._trees_of(keeper.world(), ["t2"], "2026-11-21")
            self.assertEqual(keeper.models.asked, [], "the world drawn again: its trees not made again")

    def test_each_card_shown_by_the_rooms_design_and_the_rest_given_a_thing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            room = {"legend": {"S": {"kind": "bookshelf", "name": "The sheets", "why": "Every hive.", "card": "c1"}},
                    "floor": ["........S...", "........S..."] + ["............"] * 4}
            keeper = pw.PlayWorld(Path(folder), ByTask({muse.TASK_ROOM: room, muse.TASK_ROOM_LOOK: {}}), CONFIG)
            world = keeper.world()
            world["zones"][0]["designed"] = "before"
            zone = keeper._add_zone(world, "study", "Hive census", "Counting bees.", "2026-11-19")
            zone.update(task="t1", w=12, h=8)
            world["tasks"] = {"t1": {"minutes": 9000}}
            world["work_trees"] = {"t1": muse.forest(self.CARDS)}
            keeper.save(world)
            keeper._design_rooms("2026-11-19")
            sent = json.loads(keeper.models.asked[0].messages[0]["content"])
            self.assertEqual([c["id"] for c in sent["cards"]], ["c0", "c1", "c2", "c3"], "the room told its cards")
            world = keeper.world()
            mine = [one for one in world["items"] if one["zone"] == zone["id"]]
            self.assertEqual({one.get("card") for one in mine if one.get("card")}, {"c0", "c1", "c2", "c3"},
                             "every card a thing - what the design left out, given one")
            shelf = next(one for one in mine if one.get("card") == "c1")
            self.assertEqual((shelf["kind"], shelf["card_title"], shelf["card_status"]), ("bookshelf", "Count sheet", "done"))
            charts = next(one for one in mine if one.get("card") == "c3")
            self.assertEqual((charts["kind"], charts["card_status"]), ("painting", "set_aside"))
            # The work moves on: its things' status with it.
            world["work_trees"]["t1"][1]["status"] = "working"
            keeper._cover_cards(world, "2026-11-20")
            self.assertEqual(next(one for one in world["items"] if one.get("card") == "c1")["card_status"], "working")

    def test_many_cards_designed_a_few_at_a_time_each_added_to_the_room_as_it_stands(self) -> None:
        """2026-11-19: "每个设计一次性只能输入N个卡片（N是有上限的），但是可以多次输入，最终把每个都弄好"; "N稍微小一点"."""
        cards = [{"id": "r", "parent": "", "title": "Goal", "statement": "", "type": "", "status": "working"}] + [
            {"id": f"k{n}", "parent": "r", "title": f"Part {n}", "statement": "", "type": "code", "status": "open"}
            for n in range(1, 30)]
        answers = []

        class Rooms(ByTask):
            def invoke(self, request, role=""):
                self.asked.append(request)
                if request.task != muse.TASK_ROOM:
                    return SimpleNamespace(text="{}")
                sent = json.loads(request.messages[0]["content"])
                row = len(answers) * 2                     # each batch a row of its own, its first card a chair
                answers.append(sent)
                grid = ["." * 14] * 10
                grid[row] = "C" + "." * 13
                return SimpleNamespace(text=json.dumps({"idea": f"idea {len(answers)}", "legend": {
                    "C": {"kind": "chair", "name": "Seat", "why": "w", "card": sent["cards"][0]["id"]}}, "floor": grid}))
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Rooms({}), CONFIG)
            world = keeper.world()
            world["zones"][0]["designed"] = "before"
            zone = keeper._add_zone(world, "lab", "Hive lab", "Bees.", "2026-11-19")
            zone.update(task="t1", w=14, h=12)
            world["tasks"] = {"t1": {"minutes": 9000}}
            world["work_trees"] = {"t1": muse.forest(cards)}
            keeper.save(world)
            keeper._design_rooms("2026-11-19")
            self.assertEqual([len(one["cards"]) for one in answers], [12, 12, 6], "a few cards a call, every one given")
            self.assertEqual([bool(one.get("adding")) for one in answers], [False, True, True])
            self.assertEqual(len({c["id"] for one in answers for c in one["cards"]}), 30, "no card twice")
            self.assertIn("X", "".join(answers[1]["grid"]), "what was laid before shown staying")
            world = keeper.world()
            seats = [one for one in world["items"] if one["zone"] == zone["id"] and one["kind"] == "chair"]
            self.assertEqual(len(seats), 3, "each batch's added, none let go")
            self.assertEqual(next(z for z in world["zones"] if z["id"] == zone["id"])["room_idea"], "idea 1")
            self.assertEqual(len({one.get("card") for one in world["items"] if one.get("card")}), 30, "every card a thing")

    def test_new_cards_stand_in_till_enough_then_the_room_is_designed_again_for_them(self) -> None:
        """2026-11-19: "新卡攒够了要分批加入再设计"."""
        asked = []

        class Rooms(ByTask):
            def invoke(self, request, role=""):
                self.asked.append(request)
                if request.task != muse.TASK_ROOM:
                    return SimpleNamespace(text="{}")
                sent = json.loads(request.messages[0]["content"])
                asked.append(sent)
                grid = ["." * 12] * 6
                grid[len(asked)] = "".join("ABCDEFGH"[n] for n in range(len(sent["cards"]))) + "." * (12 - len(sent["cards"]))
                return SimpleNamespace(text=json.dumps({"idea": "a study", "legend": {
                    "ABCDEFGH"[n]: {"kind": "chair", "name": c["title"], "why": "w", "card": c["id"]}
                    for n, c in enumerate(sent["cards"])}, "floor": grid}))
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Rooms({}), CONFIG)
            world = keeper.world()
            world["zones"][0]["designed"] = "before"
            zone = keeper._add_zone(world, "study", "Hive census", "Bees.", "2026-11-19")
            zone.update(task="t1", w=12, h=8)
            world["tasks"] = {"t1": {"minutes": 9000}}
            world["work_trees"] = {"t1": muse.forest(self.CARDS[:2])}
            keeper.save(world)
            keeper._design_rooms("2026-11-19")
            self.assertEqual(len(asked), 1)
            # Its work grows: one new card - a stand-in, no design.
            world = keeper.world()
            new = [{"id": f"n{n}", "parent": "c0", "title": f"New {n}", "statement": "", "type": "", "status": "open"}
                   for n in range(1, 5)]
            world["work_trees"] = {"t1": muse.forest(self.CARDS[:2] + new[:1])}
            keeper.save(world)
            keeper._design_rooms("2026-11-20")
            self.assertEqual(len(asked), 1, "one new card: a stand-in, no design yet")
            world = keeper.world()
            self.assertTrue(next(one for one in world["items"] if one.get("card") == "n1")["stand_in"])
            # Enough gathered: the room designed again for them, added to - the stand-ins gone.
            world["work_trees"] = {"t1": muse.forest(self.CARDS[:2] + new)}
            keeper.save(world)
            keeper._design_rooms("2026-11-21")
            keeper._design_rooms("2026-11-21")
            self.assertEqual(len(asked), 2)
            self.assertTrue(asked[1]["adding"])
            self.assertEqual({c["id"] for c in asked[1]["cards"]}, {"n1", "n2", "n3", "n4"})
            world = keeper.world()
            mine = [one for one in world["items"] if one["zone"] == zone["id"] and one.get("card")]
            self.assertFalse(any(one.get("stand_in") for one in mine), "the stand-ins taken away")
            self.assertEqual({one["card"] for one in mine}, {"c0", "c1", "n1", "n2", "n3", "n4"})

    def test_an_open_place_designed_card_by_card_too(self) -> None:
        """2026-11-19: "我确定一下，现在task不一定是内场吧，允许全外场之类的吧"; "露天和室内都是逐卡片的"."""
        room = {"legend": {"F": {"kind": "flowerpot", "name": "Count bed", "why": "w", "card": "c1"}},
                "floor": ["F..........."] + ["............"] * 5,
                "wall": [{"kind": "window", "x": 2, "name": "No wall here", "why": "-"}]}
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({muse.TASK_ROOM: room, muse.TASK_ROOM_LOOK: {}}), CONFIG)
            world = keeper.world()
            world["zones"][0]["designed"] = "before"
            garden = keeper._add_zone(world, "garden", "Hive garden", "Bees.", "2026-11-19")
            garden.update(task="t1", w=12, h=8)
            world["tasks"] = {"t1": {"minutes": 900}}
            world["work_trees"] = {"t1": muse.forest(self.CARDS[:2])}
            keeper.save(world)
            self.assertEqual(keeper._design_rooms("2026-11-19"), 1)
            sent = json.loads(keeper.models.asked[0].messages[0]["content"])
            self.assertTrue(sent["room"]["open_air"])
            self.assertEqual(sent["wall_width"], 0)
            world = keeper.world()
            mine = [one for one in world["items"] if one["zone"] == garden["id"]]
            self.assertIn("c1", {one.get("card") for one in mine})
            self.assertNotIn("window", {one["kind"] for one in mine}, "nothing on a wall it has not")

    def test_each_scene_its_own_edges_and_animals_chosen_and_drawable(self) -> None:
        """2026-11-19: "每个场景外围要agent写2-3种地形，5个左右的动物，agent在确定场景之后从里面挑选"."""
        web = support.ROOT / "personalized_agent" / "api" / "web"
        regions_js = (web / "play-regions.js").read_text(encoding="utf-8")
        catalogue = json.loads((web / "play" / "catalogue.json").read_text(encoding="utf-8"))["things"]
        for scene, (edges, animals) in muse.SCENE_LIFE.items():
            self.assertTrue(2 <= len(edges) + (1 if "sea" in edges else 0) <= 4, scene)
            self.assertEqual(len(animals), 5, scene)
            for edge in edges:
                self.assertIn(edge, muse.EDGE_KINDS, scene)
                if edge != "sea":
                    self.assertIn(f"  {edge}: {{ ground:", regions_js, edge)
            for animal in animals:
                self.assertTrue(animal in catalogue or f"  {animal}: [" in regions_js, f"{scene}: {animal} drawn")
        mood = muse.read_mood({"scene": "snowfield", "edges": ["dunes", "snowdrift"], "animals": ["camel", "owl"],
                               "road": {"material": "stone", "width": 7, "curve": 3}})
        self.assertEqual(mood["edges"], ["snowdrift"], "only the scene's own")
        self.assertEqual(mood["animals"], ["owl"])
        self.assertEqual(mood["road"], {"material": "stone", "width": 3, "curve": 1.0})
        self.assertIsNone(muse.road_of({"material": "lava"}), "a way of what ways are made of")
        # "海岛这些一定保留": a scene with water round it keeps it, whatever was chosen.
        self.assertIn("sea", muse.read_mood({"scene": "lone_island", "edges": ["cliffs"]})["edges"])
        self.assertIn("sea", muse.read_mood({"scene": "misty_lake", "edges": ["reeds", "moor"]})["edges"])

    def test_the_page_shows_a_things_card_and_its_status(self) -> None:
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn('if (item.card_status === "set_aside") context.globalAlpha = 0.45;', page)
        self.assertIn('if (item.card_status === "working") {', page)
        self.assertIn('playWords("Its work")', page)
        from personalized_agent.common import modules
        self.assertEqual(modules.module_of(muse.TASK_TREE), "play_world")


class OpenSidesAllOpenTests(unittest.TestCase):
    def test_an_open_side_has_nothing_standing_across_it(self) -> None:
        """2026-11-19: "半开放设计，不存在门上两个柱子的情况，一个地方开了就是这个边全空"."""
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("const pillar = y === yb && Boolean(end) && !pavilion;", page)
        self.assertNotIn("% 4", page.split("const pillar = ")[1].split("\n")[0], "no posts across the opening")
        self.assertIn("const wall = end && !pavilion;", page, "a pavilion's sides: nothing standing (2026-11-19)")


class TheWaysBetweenTheLandsTests(unittest.TestCase):
    """2026-11-19: "交通工具有一层专门的设计，包括传送门和不同的交通工具，在layout和各个场景设计完之后才设计 ... overlap比较高的
    task有更快捷的交通，然后交通工具要符合场景。另外多个互相overlap的场景用火车等交通工具环形连接"."""

    LANDS = [{"id": "r1", "biome": "snow"}, {"id": "r2", "biome": "snow"}, {"id": "r3", "biome": "forest"},
             {"id": "r4", "biome": "skycity"}, {"id": "r5", "biome": "seaside", "edges": ["sea"]},
             {"id": "r6", "biome": "lakeside", "edges": ["sea", "reeds"]}]
    TIES = [{"from": "r1", "to": "r2", "tie": 0.5}, {"from": "r1", "to": "r3", "tie": 0.4},
            {"from": "r2", "to": "r3", "tie": 0.3}, {"from": "r3", "to": "r4", "tie": 0.2}]

    def test_each_vehicle_suits_its_lands_and_close_ties_are_joined_rings_where_all_overlap(self) -> None:
        said = {"lines": [{"vehicle": "sleigh", "stops": ["r1", "r3"]},            # a sleigh in a forest: no
                          {"vehicle": "sleigh", "stops": ["r1", "r2"], "name": "Snow line"},
                          {"vehicle": "sailboat", "stops": ["r5", "r6"]},
                          {"vehicle": "airship", "stops": ["r4", "r3"]},          # the sky's own, to the ground: yes
                          {"vehicle": "train", "stops": ["r4", "r1"]}],           # a train in the sky: no
                "portals": [{"from": "r3", "to": "r4", "look": "fairy"}, {"from": "r9", "to": "r1"}],
                "roads": [{"from": "r1", "to": "r2", "material": "stone", "width": 3, "curve": 0.2},
                          {"from": "r2", "to": "r1", "material": "dirt"},                  # the same two again: no
                          {"from": "r3", "to": "r5", "material": "lava"}]}                 # not a way's material: no
        ways = muse.read_transport(said, self.LANDS, self.TIES)
        # 2026-11-19: "场景之间的道路和交通工具一样设计，是整体规划".
        self.assertEqual([(r["from"], r["to"], r["material"], r["width"]) for r in ways["roads"]], [("r1", "r2", "stone", 3)])
        lines = [(one["vehicle"], one["stops"], one["ring"]) for one in ways["lines"]]
        self.assertIn(("sleigh", ["r1", "r2"], False), lines)
        self.assertIn(("sailboat", ["r5", "r6"], False), lines)
        # 2026-11-20: "所有交通工具设置的时候要考虑场景适配性，比如天空要用天空之船，海底用潜艇，轮船，太空用宇宙飞船等" - the sky's
        # own takes one to the ground; the ground's never goes up into the sky.
        self.assertIn(("airship", ["r4", "r3"], False), lines)
        ground = ("train", "tram", "metro", "bus", "taxi", "plane", "helicopter")
        sky = [one["id"] for one in self.LANDS if one.get("biome") == "skycity"]
        self.assertFalse([one for one in ways["lines"] if one["vehicle"] in ground and set(one["stops"]) & set(sky)])
        self.assertIn(("metro", ["r1", "r2", "r3"], True), lines, "all three overlapping: a ring that suits them all")
        # 2026-11-19: "传送门取消"; "每两个大project(区域）之间至少有一个交通工具"; "联系紧密的project之间交通工具多".
        self.assertEqual(ways["portals"], [], "no portals: every way a vehicle")
        ids = [one["id"] for one in self.LANDS]
        for n, a in enumerate(ids):
            for b in ids[n + 1:]:
                self.assertTrue(any(a in one["stops"] and b in one["stops"] for one in ways["lines"]), (a, b))
        for tie in self.TIES:
            wanted = next(k for least, k in muse.TIE_LINES if float(tie.get("tie") or 0) >= least)
            self.assertGreaterEqual(sum(1 for one in ways["lines"] if tie["from"] in one["stops"] and tie["to"] in one["stops"]),
                                    wanted, tie)
        far = [{"id": "z1", "x": 0, "y": 0, "w": 10, "h": 8}, {"id": "z2", "x": 70, "y": 0, "w": 10, "h": 8},
               {"id": "z3", "x": 30, "y": 5, "w": 10, "h": 8}]
        local = muse.local_lines(far, {"id": "r1", "biome": "city"})
        self.assertEqual((local[0]["vehicle"], local[0]["places"]), ("tram", ["z1", "z3", "z2"]), "a project's far places joined")
        self.assertEqual(muse.local_lines(far[:1] + far[2:], {"id": "r1", "biome": "city"}), [], "near: none")

    def test_designed_after_the_lands_and_again_only_when_they_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            said = {muse.TASK_TRANSPORT: {"lines": [{"vehicle": "train", "stops": ["r1", "r2"], "name": "Loop"}]}}
            keeper = pw.PlayWorld(Path(folder), ByTask(said), CONFIG)
            world = keeper.world()
            world["regions"][0].pop("closed", None)
            world["regions"].append({**world["regions"][0], "id": "r2", "name": "Two"})
            world["commutes"] = [{"from": "r1", "to": "r2", "tie": 0.4, "days": 5}]
            keeper.save(world)
            self.assertTrue(keeper._design_transport("2026-11-19"))
            self.assertEqual(keeper.world()["transport"]["lines"][0]["name"], "Loop")
            self.assertFalse(keeper._design_transport("2026-11-20"), "nothing changed: not designed again")
            from personalized_agent.common import modules
            self.assertEqual(modules.module_of(muse.TASK_TRANSPORT), "play_world")

    def test_the_page_lays_the_designed_ways_each_vehicle_its_own(self) -> None:
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("for (const one of designed ? design.lines : []) {", page)
        self.assertIn('sleigh: { kind: "train", runs: "rails", engine: "reindeer", car: "sleigh", words: "Sleigh" },', page)
        self.assertIn('name: playWords("Airport to other areas"), gate: true, look: "airport"', page, "an airport, not a gate")
        self.assertIn("for (const one of (design && design.local) || []) {", page)
        regions_js = (support.ROOT / "personalized_agent" / "api" / "web" / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("const planRoads = ((world.transport || {}).roads || [])", regions_js)
        self.assertIn("!planned.has(n)) lay(playRoute(blocked, hearts[from], hearts[n]", regions_js,
                      "the old join only where no planned road reaches")
        for vehicle in muse.VEHICLES:
            self.assertIn(f"  {vehicle}: {{ kind:", page, vehicle)


class GrownOnTillCaughtUpTests(unittest.TestCase):
    """2026-11-19: "这个是停滞了吗，怎么没有adding more了" - grown at once when opened, it drew one part and stopped."""

    def test_grown_at_once_it_goes_on_part_after_part_till_caught_up(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({}), CONFIG)
            parts = []
            behind = [True, True, False]
            keeper.grow = lambda now=None: parts.append(1) or {"zones": 0, "items": 1}
            keeper.behind = lambda *a: behind[len(parts) - 1] if len(parts) <= len(behind) else False
            real, pw.CHAIN_SECONDS = pw.CHAIN_SECONDS, 0
            try:
                self.assertTrue(keeper.grow_soon())
                for _ in range(200):
                    if len(parts) >= 3:
                        break
                    import time
                    time.sleep(0.01)
            finally:
                pw.CHAIN_SECONDS = real
            self.assertEqual(len(parts), 3, "on till caught up, then no more")

    def test_the_page_keeps_looking_while_it_is_behind(self) -> None:
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn("play.growing = Boolean(said.growing || said.behind);", page)
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('"behind": bool(world_keeper.on() and world_keeper.behind()),', routes)


class InBothLanguagesTests(unittest.TestCase):
    """2026-11-19: "先不用中英文，就先default设置为user的display languagde setting。但是如果用户一开始英文，设置了切换为中文，
    会开始逐个翻译，直到所有都变成中文（翻译的结果保留，这样之后就可以切换）"."""

    @staticmethod
    def _keeper(folder, language, bare=True):
        shown = {"display_language": language}
        config = SimpleNamespace(get=lambda key, default=None: shown.get(key, default))
        keeper = pw.PlayWorld(Path(folder), ByTask({}), config)
        if bare:                                   # the translator's own: its first words not given in both yet
            world = keeper.world()
            for _key, one in keeper._word_holders(world):
                one.pop("words", None)
            keeper.save(world)
        return keeper, shown

    def test_in_the_language_shown_nothing_is_asked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper, _shown = self._keeper(folder, "en")
            keeper.world()
            self.assertEqual(keeper.translate_all(), 0)
            self.assertEqual(keeper.models.asked, [], "written in the language shown: nothing to translate")

    def test_changed_to_chinese_everything_put_into_it_and_kept_for_a_change_back(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper, shown = self._keeper(folder, "en")
            world = keeper.world()
            home, bed = world["zones"][0], world["items"][0]
            shown["display_language"] = "zh-CN"
            keeper.models.said[pw.TASK_TRANSLATE] = {
                f"zones:{home['id']}": {"name": "家", "why": "你开始和结束每一天的地方。"},
                f"items:{bed['id']}": {"why": ""}}                     # left empty: not kept, asked again
            self.assertEqual(keeper._translate(), 1)
            sent = keeper.models.asked[0]
            self.assertIn("into Simplified Chinese", sent.system, "the language said explicitly")
            self.assertIn(f"items:{bed['id']}", json.loads(sent.messages[0]["content"]))
            world = keeper.world()
            words = world["zones"][0]["words"]
            self.assertEqual(words["zh"]["name"], "家")
            self.assertEqual(words["en"]["name"], "Home", "what was written kept: a change back needs nothing")
            keeper.models.asked.clear()
            keeper._translate()
            self.assertNotIn(f"zones:{home['id']}", json.loads(keeper.models.asked[0].messages[0]["content"]),
                             "put into it once, never again")
            shown["display_language"] = "en"
            keeper.models.asked.clear()
            keeper._translate()
            self.assertEqual(keeper.models.asked, [], "back to English: its own words were kept")

    def test_words_put_into_it_once_are_remembered_for_a_world_drawn_again(self) -> None:
        # 2026-11-20: "为什么会有这么多翻译，我一开始就是中文呀" - the same titles asked again in each new world.
        with tempfile.TemporaryDirectory() as folder:
            keeper, shown = self._keeper(folder, "en")
            world = keeper.world()
            home = world["zones"][0]
            shown["display_language"] = "zh-CN"
            keeper.models.said[pw.TASK_TRANSLATE] = {f"zones:{home['id']}": {"name": "家", "why": "你开始和结束每一天的地方。"}}
            keeper._translate(only={f"zones:{home['id']}"})
            self.assertIn("Home", json.loads((Path(folder) / pw.TRANSLATED_FILE).read_text(encoding="utf-8"))["zh"])
            world = keeper.world()                       # drawn again: its words as first written
            world["zones"][0].pop("words", None)
            keeper.save(world)
            keeper.models.asked.clear()
            self.assertEqual(keeper._translate(only={f"zones:{home['id']}"}), 1)
            self.assertEqual(keeper.models.asked, [], "remembered: nothing asked")
            self.assertEqual(keeper.world()["zones"][0]["words"]["zh"]["name"], "家")

    def test_mostly_chinese_with_a_proper_noun_is_chinese(self) -> None:
        self.assertFalse(pw.PlayWorld._not_in("这是 WaterMelon 的第一个界面", "zh"))
        self.assertFalse(pw.PlayWorld._not_in("用 GitHub 和 Python 写的个性化智能体", "zh"))
        self.assertTrue(pw.PlayWorld._not_in("Personalized Agent (WaterMelon)", "zh"))
        self.assertTrue(pw.PlayWorld._not_in("读 Reading Club notes 的笔记", "zh"), "three Latin words in a row")

    def test_the_short_judging_calls_on_the_small_model(self) -> None:
        # 2026-11-20: "适合换：翻译、找图、看图、选主题、心情、事件、看一眼 ... 你可以试试".
        for task in ("play_world_translate", "play_world_find", "play_world_find_look", "play_world_themes",
                     "play_world_mood", "play_world_mood_scene", "play_world_events", "play_world_look"):
            self.assertEqual(pw.role_of(task), "fast_reply", task)
        for task in ("play_world_room", "play_world_room_look", "play_world_piece", "play_world_make", "play_world_muse",
                     "play_world_grow"):
            self.assertEqual(pw.role_of(task), "primary", task)
        source = Path(pw.__file__).read_text(encoding="utf-8")
        self.assertIn('role=role_of(task))', source)
        find = (support.ROOT / "personalized_agent" / "agent" / "playfind.py").read_text(encoding="utf-8")
        self.assertIn('role="fast_reply")', find)

    def test_a_world_just_begun_is_in_both_languages_from_the_first_moment(self) -> None:
        # 2026-11-19: "你初始的item中英翻译可以做好，这样一开始就可以切换".
        with tempfile.TemporaryDirectory() as folder:
            keeper, shown = self._keeper(folder, "zh-CN", bare=False)
            world = keeper.world()
            self.assertEqual(world["zones"][0]["words"]["zh"]["name"], "家")
            self.assertTrue(all((one.get("words") or {}).get("zh", {}).get("why") for one in world["items"]))
            # "lamp, bed这些也要翻译中文": a thing with no name called by its kind, in both
            self.assertEqual(sorted(one["words"]["zh"]["name"] for one in world["items"]), ["台灯", "床", "窗户"])
            self.assertEqual(pw.PlayWorld._words_of({"zone": "z1", "kind": "autumnTree", "name": ""})["name"], "Autumn tree")
            self.assertEqual(keeper.translate_all(), 0)
            self.assertEqual(keeper.models.asked, [], "nothing asked: already in both")
            shown["display_language"] = "en"
            self.assertEqual(keeper.translate_all(), 0)
            self.assertEqual(keeper.models.asked, [])

    def test_a_thing_looked_at_is_put_into_the_language_at_once(self) -> None:
        # 2026-11-19: "你play切换语言的时候，你look生成的话也要换语言" - looked at before the rounds reached it.
        with tempfile.TemporaryDirectory() as folder:
            keeper, shown = self._keeper(folder, "en")
            world = keeper.world()
            home = world["zones"][0]
            shown["display_language"] = "zh-CN"
            keeper.models.said[pw.TASK_TRANSLATE] = {f"zones:{home['id']}": {"name": "家", "why": "你开始和结束每一天的地方。"}}
            said = keeper.translate_one(f"zones:{home['id']}")
            self.assertEqual((said["language"], said["words"]["name"]), ("zh", "家"))
            asked = json.loads(keeper.models.asked[0].messages[0]["content"])
            self.assertEqual(list(asked), [f"zones:{home['id']}"], "that one only")
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn('api.post("/api/play/translate", { key })', page)
        self.assertIn('"feeling", "to_them", "look", "task_note", "fun_fact", "task_title"]', page, "a land's feeling and words, a card's lines, a place's task")
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('@route("POST", "/api/play/translate")', routes)

    def test_every_sign_put_in_it_explicitly(self) -> None:
        """2026-11-19: "生成标牌什么的时候，都要explicitly给language"."""
        self.assertEqual(pw.PlayWorld._words_of({"name": "Lab", "floor_text": "BEES", "card_title": "Count sheet"}),
                         {"name": "Lab", "floor_text": "BEES", "card_title": "Count sheet"})
        holders = dict(pw.PlayWorld._word_holders({"zones": [], "items": [], "regions": [], "projects": [],
                                                   "transport": {"lines": [{"name": "Snow line"}]}}))
        self.assertEqual(holders["transport:0"]["name"], "Snow line", "the lines' names too")
        self.assertIn("ONLY, EXPLICITLY", pw.TRANSLATE_SYSTEM)

    def test_all_of_it_each_growth_and_at_once_when_the_language_is_changed(self) -> None:
        source = Path(pw.__file__).read_text(encoding="utf-8")
        self.assertIn("                self.translate_all()", source)
        self.assertGreaterEqual(pw.TRANSLATE_ROUNDS_MOST * pw.TRANSLATE_MOST, 2000, "hundreds of things, all of them")
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('target=world.translate_all, name="play-world-language"', routes)
        notes = (support.ROOT / "personalized_agent" / "api" / "web" / "notes.js").read_text(encoding="utf-8")
        self.assertIn('line.addEventListener("world", () => {', notes, "shown as each batch comes")

    def test_plays_own_language_chosen_in_its_panel(self) -> None:
        """2026-11-19: "选语言的setting, 要放在play panel里（这个setting是只对play panel内生效的，即使display languagde
        还是英文，play也可以选中文）"."""
        from personalized_agent.common.config import schema
        field = schema.SCHEMA_BY_KEY["play_language"]
        self.assertTrue(field.hidden, "set from the Play panel, not the Settings page")
        self.assertEqual(field.choices, ["", "en", "zh"])
        with tempfile.TemporaryDirectory() as folder:
            keeper, shown = self._keeper(folder, "en")
            shown["play_language"] = "zh"
            self.assertEqual(keeper._shown_language(), "zh", "Play in Chinese, the display in English")
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn("data-play-language", page)
        self.assertIn('await api.post("/api/settings", { values: { play_language: next } });', page)
        self.assertIn('if (own === "en") return text;', page, "Play's own words in its own language")
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('"language": world_keeper._shown_language(),', routes)
        self.assertIn('changed = "display_language" in values or "play_language" in values', routes)

    def test_changed_while_asked_is_left_for_the_next_time(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper, shown = self._keeper(folder, "zh")
            world = keeper.world()
            home = world["zones"][0]
            real = keeper._call

            def renamed(*args, **kwargs):
                fresh = keeper.world()
                fresh["zones"][0]["name"] = "New Home"
                keeper.save(fresh)
                return {f"zones:{home['id']}": {"name": "家", "why": "w"}}
            keeper._call = renamed
            keeper._translate()
            keeper._call = real
            self.assertNotIn("words", keeper.world()["zones"][0])

    def test_only_proper_nouns_are_kept_and_an_old_kept_name_asked_again_once(self) -> None:
        # 2026-11-19: "只有英文专有名词才不翻译" - a project's descriptive name was kept in English under the old rule.
        self.assertIn("ONLY A PROPER NOUN IS KEPT AS IT IS", pw.TRANSLATE_SYSTEM)
        self.assertNotIn("A product's or a project's own name (WaterMelon, StarCatalog) is kept", pw.TRANSLATE_SYSTEM)
        self.assertTrue(pw.PlayWorld._not_in("Hive Census", "zh"))
        self.assertTrue(pw.PlayWorld._not_in("Tea with 小明", "zh"), "half English: put into Chinese too")
        self.assertFalse(pw.PlayWorld._not_in("蜂巢普查", "zh"))
        with tempfile.TemporaryDirectory() as folder:
            keeper, _shown = self._keeper(folder, "zh")
            world = keeper.world()
            home = world["zones"][0]
            home["name"] = "Hive Census Lab"
            home["words"] = {"zh": {"name": "Hive Census Lab"}, "en": {"name": "Hive Census Lab"}}   # the old rule's
            keeper.save(world)
            key = f"zones:{home['id']}"
            keeper._call = lambda *args, **kwargs: {k: {f: ("蜂巢普查室" if f == "name" else "中文") for f in need}
                                                    for k, need in json.loads(args[2]).items()}
            self.assertGreaterEqual(keeper._translate(), 1)
            words = keeper.world()["zones"][0]["words"]
            self.assertEqual(words["zh"]["name"], "蜂巢普查室")
            self.assertEqual(words["en"]["name"], "Hive Census Lab")
            self.assertEqual(words["rule"], pw.TRANSLATE_RULE)
            keeper._call = lambda *args, **kwargs: {key: {"name": "Hive Census Lab"}}    # kept English: once only
            world = keeper.world()
            world["zones"][0]["words"]["zh"]["name"] = "ChatGPT 阅读"
            keeper.save(world)
            asked = []
            keeper._call = lambda *args, **kwargs: asked.append(args) or {}
            keeper._translate()
            self.assertFalse([a for a in asked if key in str(a)], "under the new rule, never asked again")

    def test_a_project_large_enough_becomes_one_great_ground_of_its_own_shape(self) -> None:
        # 2026-11-19: "能不能这样整体变成一个更融合的design, 而不是一个个block"; "记录面积和位置之后要选择形状，重新设计内容";
        # "让agent可以进行一组主动搜素，描述地区附近的场景"; "融合之后不要独立房子".
        from personalized_agent.agent import playgreat as great
        for kind in great.SHAPES:                       # each outline: inside its box, more than a few tiles
            tiles = great.shape_tiles({"kind": kind, "seed": 1.0}, (10, 20, 40, 24))
            self.assertTrue(80 < len(tiles) <= 960, kind)
            self.assertTrue(all(10 <= x < 50 and 20 <= y < 44 for x, y in tiles), kind)
        regions_js = (support.ROOT / "personalized_agent" / "api" / "web" / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("if (zone.shape && !playShapeHas(zone.shape, [fx, fy, fw, fh], x, y)) continue;", regions_js)
        survey = {"surroundings": "A wooded valley.", "mode": "mixed", "shape": "ellipse",
                  "references": [{"name": "A hillside monastery", "traits": "Terraces.", "url": "https://example.org/m"}]}
        plan = {"idea": "One valley farm.", "parts": [
                    {"task": "t1", "kind": "garden", "cell": [1, 1, 3, 2], "shape": "blob", "why": "At its heart."},
                    {"task": "t2", "kind": "studio", "cell": [5, 1, 4, 3], "why": "The hall."},
                    {"task": "nobody", "kind": "garden", "cell": [0, 0, 1, 1]}],
                "stages": [{"title": "Seeds", "cards": ["a1", "a2"], "cell": [2, 2]}, {"title": "Fruit", "cards": ["b1"]}],
                "featured": [{"card": "b1", "why": "It fought back."}, {"card": "elsewhere"}]}
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({great.TASK_GREAT_SURVEY: survey, great.TASK_GREAT_PLAN: plan}),
                                  CONFIG)
            world = keeper.world()
            made = []
            for n, (task, kind) in enumerate((("t1", "garden"), ("t2", "studio"), ("t3", "library"))):
                zone = keeper._add_zone(world, kind, f"Place {n}", "Work.", "2026-10-01")
                zone.update(task=task, w=14, h=8, days=["2026-10-01", "2026-10-09"])
                made.append(zone)
            world["projects"] = [{"id": "p1", "name": "Valley Farm", "tasks": ["t1", "t2", "t3"]}]
            world["work_trees"] = {"t1": [{"id": "a1", "parent": "", "title": "Sow", "status": "done", "depth": 0,
                                           "minutes": 30},
                                          {"id": "a2", "parent": "a1", "title": "Water", "status": "done", "depth": 1}],
                                   "t2": [{"id": "b1", "parent": "", "title": "Harvest", "status": "working", "depth": 0,
                                           "minutes": 900, "tries": 5}]}
            keeper.save(world)
            self.assertEqual(keeper._great_grounds("2026-11-19"), 1)
            asked = {r.task: r for r in keeper.models.asked}
            self.assertTrue(asked[great.TASK_GREAT_SURVEY].builtin_web, "the site surveyed, the web searched")
            here = asked[great.TASK_GREAT_SURVEY].messages[0]["content"]
            self.assertNotIn("Valley Farm", here, "nothing of the project in the survey")
            guard = asked[great.TASK_GREAT_SURVEY].web_guard          # 2026-11-20: "这一步没有加搜索检查...这个改就行"
            self.assertIsNotNone(guard, "the search's guard on the survey")
            # 2026-11-20: "搜索检查只查密码邮箱之类，项目名人名都不拦".
            self.assertIsNone(guard.check_query("Valley Farm site plan"), "a project's name let through")
            self.assertIsNotNone(guard.check_query("ask farmer@example.org"), "a mail address stopped")
            sent = json.loads(asked[great.TASK_GREAT_PLAN].messages[0]["content"])
            self.assertNotIn("feeling", sent, "the feeling only chooses the scene")
            self.assertEqual(sent["work"]["t2"][0]["minutes"], 900)
            world = keeper.world()
            ground = next(z for z in world["zones"] if z.get("great") == "p1")
            self.assertEqual(ground["shape"]["kind"], "ellipse")
            self.assertFalse([z for z in world["zones"] if z.get("plot") and not z.get("great")], "its old blocks gone")
            parts = {z["task"]: z for z in world["zones"] if z.get("in_great") == "p1"}
            self.assertEqual(set(parts), {"t1", "t2", "t3"}, "every task a part - one the plan left out placed")
            self.assertEqual(parts["t1"]["shape"]["kind"], "blob")
            self.assertTrue(parts["t1"]["designed"], "an open part designed with the ground")
            self.assertNotIn("designed", parts["t2"], "a hall designed as a room")
            box = (ground["x"], ground["y"] + 1, ground["w"], ground["h"])
            floor = keeper._floor_tiles(ground)
            self.assertTrue(floor and all(great.shape_has(ground["shape"], box, x, y) for x, y in floor))
            x0, y0, x1, y1 = places.zone_rect(parts["t2"])
            self.assertTrue(box[0] <= x0 and x1 <= box[0] + box[2] and box[1] <= y0 and y1 <= box[1] + box[3], "inside it")
            kept = world["great"]["p1"]
            self.assertEqual([(s["title"], s["done"], s["of"]) for s in kept["stages"]], [("Seeds", 2, 2), ("Fruit", 0, 1)])
            self.assertEqual([f["card"] for f in kept["featured"]], ["b1"], "the agent's hardest, its own cards only")
            brief = keeper._great_brief(world, ground)
            self.assertEqual(brief["featured"][0]["title"], "Harvest")
            world["work_trees"]["t2"][0]["status"] = "done"   # the work goes on: its stage as far as it is now
            self.assertTrue(pw.PlayWorld._great_progress(world, "p1"))
            self.assertEqual(world["great"]["p1"]["stages"][1]["done"], 1)
            keeper.save(world)
            self.assertEqual(keeper._great_grounds("2026-11-20"), 0, "not grown, the same tasks: not designed again")
            rooms = [z for z in keeper.world()["zones"] if (not z.get("plot") or z.get("great")) and not z.get("designed")]
            self.assertIn(ground["id"], [z["id"] for z in rooms], "its open ground designed with the rooms")

    def test_each_theme_has_its_own_things_to_hang_drawn_by_the_page(self) -> None:
        # 2026-11-19: "现在为什么还是大量用灯笼这个挂件"; "挂件做点水晶吊灯，南瓜灯"; "每个主题都做一个expansion ... 让model选";
        # "不要加上限".
        from personalized_agent.agent import playcatalogue as catalogue
        themes = {x for group in catalogue.theme_tree().values() for x in group}
        self.assertTrue(themes <= set(pw.THEME_HANGINGS), themes - set(pw.THEME_HANGINGS))
        for kind in ("crystal_chandelier", "pumpkin_lantern", "chandelier", "hanging_plant"):
            self.assertTrue(pw.ITEM_KINDS[kind]["hangs"], kind)
        self.assertIn("{hangings}", muse.ROOM_SYSTEM)
        self.assertFalse(hasattr(muse, "no_kind_too_often"), "no cap on a kind")
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn('fetch("/play/hangings.json")', page)
        self.assertIn("playThemeHangingDraw(context, play.themeHangings[item.kind], x, y)", page)

    def test_no_name_declared_twice_across_the_play_scripts(self) -> None:
        # 2026-11-19: "我说这个点不了了" - PLAY_MATERIALS declared in play-sound.js and again in play.js: the page loads
        # them all into one scope, play.js failed to load, and the Play button did nothing.
        web = support.ROOT / "personalized_agent" / "api" / "web"
        html = (web / "notes.html").read_text(encoding="utf-8")
        scripts = re.findall(r'<script src="/(play[\w-]*\.js)"></script>', html)
        self.assertEqual(scripts, ["play-sound.js", "play-regions.js", "play-sky.js", "play.js", "play-act.js"])
        seen: dict = {}
        for name in scripts:
            for line in (web / name).read_text(encoding="utf-8").splitlines():
                found = re.match(r"^(?:const|let|var|function|class|async function)\s+([A-Za-z_$][\w$]*)", line)
                if found:
                    seen.setdefault(found.group(1), []).append(name)
                listed = re.match(r"^(?:const|let|var)\s+(.*)$", line)
                for more in re.findall(r",\s*([A-Za-z_$][\w$]*)\s*=", listed.group(1)) if listed else []:
                    seen.setdefault(more, []).append(name)
        twice = {k: v for k, v in seen.items() if len(v) > 1}
        self.assertEqual(twice, {}, "a name declared twice stops the second script from loading at all")

    def test_every_play_call_in_plays_own_language(self) -> None:
        # 2026-11-19: "每次调用的话都要用play的语言，只是在一开始play语言等于display languagde".
        from personalized_agent.common.languages import in_language
        self.assertIn("WRITE IT IN THIS LANGUAGE: Chinese", in_language("zh", '"name"').instruction(None))
        with tempfile.TemporaryDirectory() as folder:
            shown = {"display_language": "en", "play_language": "zh"}
            config = SimpleNamespace(get=lambda key, default=None: shown.get(key, default))
            keeper = pw.PlayWorld(Path(folder), ByTask({}), config)
            keeper._call("system", "play_world_room", "{}", 100, '"name"')
            self.assertEqual(keeper.models.asked[-1].language, in_language("zh", '"name"'))
            shown["play_language"] = ""
            keeper._call("system", "play_world_room", "{}", 100, '"name"')
            self.assertEqual(keeper.models.asked[-1].language, in_language("en", '"name"'), "the display language at first")
        source = (support.ROOT / "personalized_agent" / "agent" / "playworld.py").read_text(encoding="utf-8")
        self.assertNotIn("language=in_display(", source, "no growth call in the display language")

    def test_a_lands_title_says_its_feeling_and_the_agents_words_and_rooms_follow_its_look(self) -> None:
        # 2026-11-19: "每个区域的title, 要加上用户心情，和agent说的话，比如安慰什么的"; "墙壁，地板的style再给不同的素材";
        # "区域的整体材质主题和风格的选择会影响内饰，允许模型自己create新的item, 材质 ... 保存到素材里（写description), 允许复用".
        from personalized_agent.agent import playmade as made
        mood = muse.read_mood({"scene": "snowfield", "feeling": "Tired, and a little alone", "to_them": "Rest - you did enough.",
                               "look": "Ice and pale timber."}, "Long cold days.")
        self.assertEqual((mood["feeling"], mood["to_them"], mood["look"]),
                         ("Tired, and a little alone", "Rest - you did enough.", "Ice and pale timber."))
        self.assertIn('"to_them": "<what you say to them now', muse.MOOD_SCENE_SYSTEM)
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn('feeling: region.feeling || "", toThem: region.to_them || ""', page)
        self.assertIn("for f in (\"name\", \"why\"", (support.ROOT / "personalized_agent" / "agent" / "playworld.py")
                      .read_text(encoding="utf-8").replace("'", '"') or "", "translated with the rest")
        self.assertIn("feeling", pw.PlayWorld._words_of({"feeling": "Tired"}))
        floors, walls = made.materials()["floors"], made.materials()["walls"]
        for one in ("ice", "white_marble", "parquet", "stone"):
            self.assertIn(one, floors)
        for one in ("ice", "marble", "timber", "stone"):
            self.assertIn(one, walls)
        with tempfile.TemporaryDirectory() as folder:
            drawn = {"made": [{"name": "Glow Bench", "svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 16">'
                                                          '<rect x="0" y="6" width="32" height="10" fill="#9ff0ff"/>'
                                                          '<rect x="2" y="2" width="28" height="4" fill="#5ad1ff"/></svg>',
                               "description": "A bench of light."},
                              {"name": "frost tiles", "svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                                                           '<rect width="16" height="16" fill="#dff2ff"/><circle cx="8" cy="8" '
                                                           'r="3" fill="#ffffff"/></svg>'}]}
            keeper = pw.PlayWorld(Path(folder), ByTask({made.TASK_MAKE: drawn}), CONFIG)
            zone = keeper.world()["zones"][0]
            said = {"make": [{"kind": "thing", "name": "glow bench", "description": "A bench of light.", "w": 2, "h": 1},
                             {"kind": "floor", "name": "frost tiles", "description": "Frosted tiles."},
                             {"kind": "thing", "name": "chair", "w": 1, "h": 1},                  # a kind already: no
                             {"kind": "thing", "name": "huge thing", "w": 6, "h": 6}],            # too large: no
                    "floor_material": "frost tiles", "wall_material": "ice", "floor": ["..."]}   # its grid its own key
            self.assertEqual(keeper._make_asked(said, zone, "Ice and pale timber."), 2)
            kept = json.loads((Path(folder) / made.MADE_FILE).read_text(encoding="utf-8"))
            self.assertEqual(kept["items"]["glow_bench"]["size"], [2, 1])
            self.assertEqual(kept["items"]["glow_bench"]["description"], "A bench of light.")
            self.assertIn("frost_tiles", kept["floors"])
            self.assertEqual(pw.ITEM_KINDS["glow_bench"]["size"], (2, 1), "a kind at once: the design's use of it stands")
            keeper._surfaces(zone, said)
            self.assertEqual((zone["floor_material"], zone["wall_material"]), ("frost_tiles", "ice"))
            again = pw.PlayWorld(Path(folder), ByTask({}), CONFIG)
            self.assertIn("glow_bench 2x1 (A bench of light.)", made.room_made_note(again._made_library()), "used again")
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('"made": made', routes)
        regions_js = (support.ROOT / "personalized_agent" / "api" / "web" / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("playFloorPiece(zone.floor_material)", regions_js)
        self.assertIn("const tint = wallMat ? wallMat.tint : PLAY_STYLE_WALLS[zone.style];", regions_js)

    def test_walls_of_their_own_material_rooms_as_large_as_their_work_and_outlines_of_any_shape(self) -> None:
        # 2026-11-19: "房间的墙壁是不是只有土色和灰色，加多种形态，比如冰块，石块，木头之类"; "对于这种过分拥挤的，可以给与
        # 更大的位置"; "每个区域都可以选其他形状，比如五角星，三角形，圆形等" - "每个项目的整片土地, 每个露天场地, 每个室内房间".
        from personalized_agent.agent import playgreat as great
        from personalized_agent.agent import playmade as made
        from personalized_agent.agent import playshapes as shapes
        import xml.etree.ElementTree as ET
        web = support.ROOT / "personalized_agent" / "api" / "web"
        walls = json.loads((web / "play" / "materials.json").read_text(encoding="utf-8"))["walls"]
        for name in ("ice", "stone", "timber", "logs", "brick", "bamboo", "crystal", "snow"):
            self.assertIn(name, walls)
        for name, one in walls.items():
            ET.fromstring(one["svg"])                            # each its own face, drawn
            self.assertIn(one["set"], made.WALL_SETS, name)
        page, regions_js = (web / "play.js").read_text(encoding="utf-8"), (web / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("function playWallTile(piece) {", page)
        self.assertIn('+ (wallMat && wallMat.svg ? `^${zone.wall_material}` : "");', regions_js)
        self.assertIn('"floor_material": "<its floor\'s material>"', muse.ROOM_SYSTEM, "never the grid's own key")
        self.assertIn('"kind": "thing", "floor" or "wall"', made.room_made_note({}))
        self.assertNotIn("{{", made.MAKE_SYSTEM, "sent as it is: single braces")
        self.assertEqual(made.read_asked({"make": [{"kind": "wall", "name": "Lava rock", "description": "Hot."}]}, {},
                                         lambda n: False), [{"kind": "wall", "name": "lava_rock", "description": "Hot."}])
        # rooms as large as their work
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({}), CONFIG)
            world = keeper.world()
            zone = world["zones"][0]
            before = len(keeper._floor_tiles(zone))
            self.assertTrue(keeper._fit_to_cards(world, zone, 20))
            self.assertGreaterEqual(len(keeper._floor_tiles(zone)), keeper.ROOM_BASE_TILES + keeper.TILES_A_CARD * 20)
            self.assertGreater(len(keeper._floor_tiles(zone)), before)
            self.assertFalse(keeper._fit_to_cards(world, zone, 2), "never made smaller")
        self.assertIn("EACH PART AS LARGE AS ITS WORK", great.PLAN_SYSTEM)
        # how open a house is: the agent's, none favoured
        self.assertIn("HOW OPEN IT IS IS YOURS TO CHOOSE", pw.SYSTEM)
        self.assertNotIn("think of the open and half-open first", pw.SYSTEM)
        # outlines: the page's the same points as here
        for name, points in shapes.POLYGONS.items():
            self.assertIn(f"  {name}: {json.dumps([[x, y] for x, y in points])}", regions_js)
        self.assertTrue(shapes.polygon_has("star", 0, -0.9) and not shapes.polygon_has("star", 0.9, -0.9))
        self.assertTrue(great.shape_has({"kind": "triangle"}, (0, 0, 10, 10), 5, 8))
        self.assertFalse(great.shape_has({"kind": "triangle"}, (0, 0, 10, 10), 0, 0))
        land = {"id": "r1", "ox": 0, "oy": 0, "shape": "star"}
        self.assertAlmostEqual(places.shape_limit(land, -1.5708), places.CHOSEN_REACH * shapes.reach("star", -1.5708), 6)
        # 2026-11-20: "你的主题地块一定是不规则的" - a land never takes a ruled outline.
        self.assertNotIn("shape", muse.read_mood({"scene": next(iter(muse.MOOD_SCENES)), "shape": "heart"}))
        self.assertNotIn("THE LAND'S OWN OUTLINE", muse.mood_scene_system(), "no outline asked for a land")
        room = {"kind": "study", "x": 3, "y": 5, "w": 21, "h": 13, "door": 14, "shape": {"kind": "star", "seed": 0}}
        floor = pw.PlayWorld.shaped_room(room, *pw.PlayWorld.floor_of(room))
        self.assertIn((14, 8 + 13 - 1), floor, "the way in from its door")
        self.assertNotIn((4, 8), floor, "past its outline: no floor")
        self.assertIn("function playShapedRoom(zone, door) {", regions_js)
        hall = {**room, "id": "z9", "great": "p1"}                # a great ground's hall: the same, walls along it
        self.assertEqual(object.__new__(pw.PlayWorld)._floor_tiles(hall), floor)
        self.assertIn("if (outlined) {", regions_js)
        self.assertIn("const shapeOut = playLandShapeOut(regions[a], x, y);", regions_js)
        self.assertIn('ITS OUTLINE ("shape", any place, open or under a roof', pw.SYSTEM)
        # the ways added to, a few dropped at most, never a limit
        lands = [{"id": "a", "biome": "city"}, {"id": "b", "biome": "city"}, {"id": "c", "biome": "city"}]
        now = {"lines": [{"vehicle": "train", "stops": ["a", "b"], "ring": False, "name": "Old"}], "roads": []}
        kept = muse.read_transport({"lines": [{"vehicle": "metro", "stops": ["a", "c"], "name": "New"}]}, lands, [], now)
        self.assertEqual([one["name"] for one in kept["lines"]][:2], ["Old", "New"])
        dropped = muse.read_transport({"drop": ["L1"]}, lands, [], now)
        self.assertNotIn("Old", [one["name"] for one in dropped["lines"]])
        many = muse.read_transport({"lines": [{"vehicle": "bus", "stops": ["a", "b"], "name": f"n{k}"} for k in range(30)]},
                                   lands, [], None)
        self.assertGreaterEqual(len(many["lines"]), 30, "no limit")
        self.assertIn('"ways_now"', muse.transport_system())

    def test_the_page_is_given_the_work_as_it_is_now(self) -> None:
        # 2026-11-19 (a side agent): a card done today showed its widget ripe only when the world grew.
        now = {"t1": [{"id": "a1", "status": "done", "minutes": 40}]}
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({}), CONFIG, cards=lambda ids: now)
            world = {"work_trees": {"t1": [{"id": "a1", "parent": "", "title": "Sow", "status": "open", "depth": 0}]},
                     "items": [{"id": "i1", "kind": "widget", "widget": "field_melon", "card": "a1", "card_status": "open"}],
                     "great": {"p1": {"tasks": ["t1"], "stage_groups": [{"title": "Seeds", "cards": ["a1"]}], "stages": []}}}
            self.assertTrue(keeper.cards_now(world))
            self.assertEqual(world["work_trees"]["t1"][0]["status"], "done")
            self.assertEqual(world["items"][0]["card_status"], "done")
            self.assertEqual(world["great"]["p1"]["stages"][0]["done"], 1)
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn("world_keeper.cards_now(world)", routes)

    def test_signs_never_over_one_another_and_the_first_minute_is_watched(self) -> None:
        # 2026-11-19: "你很多门牌都互相遮挡，要用代码防止这个"; "又加载play卡住了" (watched longer); "狗的叫声 ... 不够清脆".
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn("const box = playSignBox(one.sign, one.x, one.y, size, fontPx);", page)
        # 2026-11-20: "我要的就是按住就连跳" - held, it jumps again each time it lands, walking or standing.
        self.assertIn("if (play.spaceHeld && playJumpHeight() === 0) playJump();", page)
        self.assertIn('if (event.key === " ") play.spaceHeld = false;', page)
        self.assertIn("function playPostDraw(sign, x, y, size, fontPx, lift = 0) {", page)
        self.assertIn("const PLAY_WATCH_MS = 60000", page)
        self.assertIn('observe({ type: "longtask", buffered: false })', page)
        self.assertIn("setTimeout(playNoteSend, PLAY_WATCH_MS);", page)
        sound = (support.ROOT / "personalized_agent" / "api" / "web" / "play-sound.js").read_text(encoding="utf-8")
        self.assertIn('dog: [{ noise: true, band: 2600', sound, "a bark's sharp start")
        # 2026-11-19: "我刚刚有音乐，突然就没有了" - a tune left for a recorded track, and back, never started again.
        self.assertIn('  playSound.tune = null;\n  playSound.tuneKey = "";', sound)
        self.assertIn("if (tune.key !== playSound.tuneKey || !playSound.tune) {", sound)
        self.assertIn('  if (!keepName) playSound.trackName = "";', sound)
        self.assertIn('for (const kind of ["pointerdown", "keydown"]) {', sound, "a held sound let go at the first press")

    def test_a_place_may_ask_for_great_pieces_each_built_in_a_call_of_its_own(self) -> None:
        # 2026-11-19: "模型先判断是否要做大件design ... 然后进入大件design 的call, 然后用类似过山车的构筑方法做";
        # "数量要根据面积挂钩"; "都允许"; "允许交互".
        from personalized_agent.agent import playpieces as pieces
        self.assertEqual((pieces.pieces_allowed(100), pieces.pieces_allowed(800), pieces.pieces_allowed(10 ** 5)), (1, 5, 6))
        rows = ["." * 12] * 8
        asked = pieces.read_asked({"pieces": [{"kit": "stack", "what": "A tower.", "x": 1, "y": 1, "w": 4, "h": 4, "card": "c1"},
                                              {"kit": "track", "x": 2, "y": 2, "w": 4, "h": 4},          # on the tower: no
                                              {"kit": "magic", "x": 6, "y": 0, "w": 4, "h": 4},          # no such kit
                                              {"kit": "water", "x": 6, "y": 4, "w": 2, "h": 2}]},        # too small
                                  rows, 10, 20, set(), 3)
        self.assertEqual([(a["kit"], a["x"], a["y"]) for a in asked], [("stack", 11, 21)])
        self.assertIn('"pieces_most"', pieces.room_pieces_note())
        self.assertIn("MILESTONES, A STAGE'S RESULT AND ANYTHING SPECIAL ARE BEST A GREAT PIECE", pieces.room_pieces_note())
        self.assertIsNone(pieces.read_piece({"points": [[1, 1], [2, 2]]}, "track", 6, 6), "a track of two points: none")
        tower = {"segments": [{"kind": "base", "height": 1}, {"kind": "body", "height": 30, "width": 0.5},
                              {"kind": "spire", "height": 2}], "name": "The Tower", "why": "It stood out."}
        spec = pieces.read_piece(tower, "stack", 4, 4)
        self.assertLessEqual(sum(s["height"] for s in spec["segments"]), pieces.HEIGHT_MOST + 4 + 0.05, "never too tall")
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({pieces.TASK_PIECE: tower, pieces.TASK_PIECE_LOOK: {}}), CONFIG)
            world = keeper.world()
            zone = world["zones"][0]
            left, top, _w, _h = keeper.floor_of(zone)
            world["items"].append({"id": "i900", "zone": zone["id"], "kind": "chair", "x": left + 1, "y": top + 1})
            keeper.save(world)
            made = keeper._build_pieces(zone["id"], [{"kit": "stack", "what": "A tower.", "x": left, "y": top, "w": 4, "h": 4}],
                                        "2026-11-19")
            self.assertEqual(made, 1)
            asked_tasks = [r.task for r in keeper.models.asked]
            self.assertEqual(asked_tasks, [pieces.TASK_PIECE, pieces.TASK_PIECE, pieces.TASK_PIECE_LOOK],
                             "no picture drawn: its kit's parts instead, then looked at once")
            world = keeper.world()
            piece = next(one for one in world["items"] if one["kind"] == "piece")
            self.assertEqual((piece["kit"], piece["interact"], pw.item_size(piece)), ("stack", "climb", (4, 4)))
            self.assertFalse([one for one in world["items"] if one["id"] == "i900"], "what stood on its ground let go")
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn("layer(\"pieces\", () => playPiecesDraw(size));", page)
        self.assertIn("function playPieceAct(item) {", page)
        act = (support.ROOT / "personalized_agent" / "api" / "web" / "play-act.js").read_text(encoding="utf-8")
        self.assertIn('if (near && near.kind === "piece" && typeof playPieceAct === "function" && playPieceAct(near)) return true;', act)

    def test_a_great_piece_is_drawn_first_then_built_after_its_picture(self) -> None:
        # 2026-11-19: "大件是不是应该先根据模型描述，画一个图片，然后跟着图片搭"; "用'模型画 SVG'来出图"; style A (fine pixels).
        from personalized_agent.agent import playpieces as pieces
        drawn = {"svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 256">'
                        '<g data-stage="1"><rect x="0" y="208" width="64" height="48" fill="#888888"/></g>'
                        '<g data-stage="2"><rect x="8" y="80" width="48" height="128" fill="#c96f4a" onclick="x()"/>'
                        '<script>bad()</script><image href="http://elsewhere/a.png"/></g>'
                        '<rect id="entrance" x="16" y="240" width="32" height="16" fill="#333333"/>'
                        '<path id="track" d="M4 200 C 20 100, 44 100, 60 200 Z" fill="none" stroke="#d9382f"/>'
                        '<circle class="spin" data-cx="32" data-cy="60" cx="32" cy="60" r="10" fill="url(http://evil/x)"/></svg>',
                 "name": "The Tower", "why": "It stood out."}
        read = pieces.read_svg(drawn, 4, 4)
        for gone in ("script", "onclick", "image", "evil"):
            self.assertNotIn(gone, read["svg"], f"nothing unsafe kept: {gone}")
        self.assertEqual((read["stages"], read["entrance"], read["track"], read["moving"]), (2, [1, 2], True, 1))
        self.assertIn('viewBox="0 0 64 256"', read["svg"], "its canvas the piece's")
        self.assertEqual(pieces.render_svg(read["svg"], 4, 4)["media_type"], "image/png", "drawn here to be looked at")
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({pieces.TASK_PIECE: drawn, pieces.TASK_PIECE_LOOK: {}}), CONFIG)
            world = keeper.world()
            zone = world["zones"][0]
            left, top, _w, _h = keeper.floor_of(zone)
            self.assertEqual(keeper._build_pieces(zone["id"], [{"kit": "stack", "what": "A tower.", "x": left, "y": top,
                                                                "w": 4, "h": 4}], "2026-11-19"), 1)
            self.assertEqual([r.task for r in keeper.models.asked], [pieces.TASK_PIECE, pieces.TASK_PIECE_LOOK])
            self.assertIn("ONE GREAT PIECE", keeper.models.asked[0].system)
            piece = next(one for one in keeper.world()["items"] if one["kind"] == "piece")
            self.assertTrue(piece["spec"]["svg"].startswith("<svg"))
            self.assertEqual(piece["interact"], "ride", "its picture has a track: ridden")
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn("if (item.spec && item.spec.svg) { playSvgPieceDraw(item, size, now); playBoatsDraw(item, size, now); continue; }", page)
        self.assertIn("const PLAY_FINE = 2, PLAY_FINE_COLOURS = 24;", page)   # 2026-11-20: two, for memory
        self.assertIn("const step = Math.max(1, Math.floor(n / PLAY_FINE_SAMPLES));", page, "its palette from a sample")
        # "这套'把图转成精细像素'的流程 ... 也用到用户的图片、像素画和雕像上": pictures, statues and the agent's paintings.
        self.assertIn("layer(\"fine things\", () => playFineThingsDraw(size));", page)
        self.assertIn("frame: [5, 6, 22, 20], statue_of: [2, 4, 12, 18]", page)
        self.assertIn('"svg": "<your own painting of it', muse.MUSE_SYSTEM)

    def test_each_room_is_given_their_pictures_and_hangs_them(self) -> None:
        # 2026-11-19: "你现在在构建世界的时候，还是要让agent知道可以加入你自己的成果之类的信息（你现在好像没有用户的图片了），
        # 比如挂自己的图片之类" - rooms designed on their own had been given none, and no picture's kind was theirs.
        from PIL import Image
        self.assertIn("THEIR OWN WORK, ON SHOW", muse.ROOM_SYSTEM)
        self.assertIn('"picture": "<one of their_pictures\' ids', muse.ROOM_SYSTEM)
        self.assertEqual(pw.PlayWorld._room_size("poster"), (2, 2), "a picture's kind may be a room's")
        with tempfile.TemporaryDirectory() as folder:
            shot = Path(folder) / "bean-sorter-demo.png"
            image = Image.new("RGB", (64, 48), "#2a6f3a")
            for x in range(0, 64, 8):
                for y in range(0, 48, 6):
                    image.putpixel((x, y), (240, 200, 40))
            image.save(shot)
            keeper = pw.PlayWorld(Path(folder) / "world", ByTask({}), CONFIG)
            world = keeper.world()
            zone = world["zones"][0]
            keeper._pictures_by_task = {"t1": [{"id": "p1", "path": str(shot), "what": "the sorter's first demo",
                                                "when": "2026-11-02"}]}
            keeper._pictures_anywhere = []
            given = keeper._room_pictures(world, zone, "t1")
            self.assertEqual(list(given), ["p1"])
            left, top, _w, _h = keeper.floor_of(zone)
            laid = {"floor": [{"kind": "statue_of", "x": left + 1, "y": top + 1, "name": "No picture"}],   # a cartoon statue
                    "wall": [{"kind": "poster", "x": zone["x"] + 2, "name": "The first demo", "picture": "p1"}],
                    "under": [], "on": [], "hang": []}
            self.assertTrue(keeper._lay_room(world, zone, laid, "2026-11-19", "", ""))
            poster = next(one for one in world["items"] if one["kind"] == "poster")
            # 2026-11-19: "这个地图上应该画的是卡通物品，然后交互细节里有图片": a cartoon poster, the picture in its card.
            self.assertEqual((poster["shot_of"], poster.get("picture")), ("p1", ""))
            self.assertTrue((Path(folder) / "world" / "pictures" / poster["shot"]).is_file(), "a copy kept with it")
            self.assertTrue([one for one in world["items"] if one["kind"] == "statue_of"], "a cartoon statue, none of theirs")
            self.assertEqual(keeper._room_pictures(world, zone, "t1"), {}, "never hung twice")
        # "对图片的处理是可以直接放，也可以用像素风拼出某个图片（交给大item design之类)": a great piece built after one.
        from personalized_agent.agent import playpieces as pieces
        self.assertIn("BUILT AFTER IT as a great piece", pieces.room_pieces_note())
        self.assertIn("BUILD ONE AFTER ITS PICTURE", muse.ROOM_SYSTEM)
        asked = pieces.read_asked({"pieces": [{"kit": "composite", "what": "Their demo.", "x": 0, "y": 0, "w": 4, "h": 4,
                                               "picture": "p2"}]}, ["." * 8] * 6, 0, 0, set(), 2)
        self.assertEqual(asked[0]["picture"], "p2")
        with tempfile.TemporaryDirectory() as folder:
            shot = Path(folder) / "demo.png"
            Image.new("RGB", (900, 500), "#c94f3a").save(shot)
            self.assertEqual(pieces.photo_block(shot)["media_type"], "image/png")
            keeper = pw.PlayWorld(Path(folder) / "world", ByTask({}), CONFIG)
            zone = keeper.world()["zones"][0]
            left, top, _w, _h = keeper.floor_of(zone)
            keeper._room_offer = {zone["id"]: {"p2": {"id": "p2", "path": str(shot), "what": "the demo"}}}
            keeper._build_pieces(zone["id"], [{**asked[0], "x": left, "y": top}], "2026-11-19")
            first = keeper.models.asked[0]
            self.assertEqual([part["type"] for part in first.messages[0]["content"]], ["text", "image"],
                             "their picture shown to the piece's designer")
            self.assertIn("after_their_picture", first.messages[0]["content"][0]["text"])
            kit = keeper.models.asked[1]                         # no drawing came: its kit's parts, after it too
            self.assertEqual([part["type"] for part in kit.messages[0]["content"]], ["text", "image"])
            self.assertIn("build this piece AFTER IT", kit.system)

    def test_a_thing_carries_its_screenshot_in_its_card_the_original_a_press_away(self) -> None:
        # 2026-11-19: "item本身显示的不是图片，但是你打开的细节会展示图片（也就是item会和截图connect起来)"; "让模型能放上截图就放
        # 截图"; "图片用户可以点击，显示原图"; "在地图上所有图片尽可能是像素风，真实图片都藏在细节里，或者是用像素拼成的".
        from PIL import Image
        self.assertIn("ANY THING MAY ALSO ASK FOR A PICTURE IN ITS CARD", pw.SYSTEM)
        self.assertIn("ON THE MAP CARTOON THINGS ONLY, NEVER A SCREENSHOT", pw.SYSTEM)
        self.assertIn("A PICTURE IN EACH THING'S CARD", muse.ROOM_SYSTEM)
        with tempfile.TemporaryDirectory() as folder:
            shot = Path(folder) / "an-invitation.png"
            image = Image.new("RGB", (300, 200), "#eeeeee")
            for x in range(0, 300, 10):
                image.putpixel((x, x % 200), (20, 60, 200))
            image.save(shot)
            keeper = pw.PlayWorld(Path(folder) / "world", ByTask({}), CONFIG)
            world = keeper.world()
            zone = world["zones"][0]
            keeper._room_offer = {zone["id"]: {"p9": {"id": "p9", "path": str(shot), "what": "the invitation"}}}
            left, top, _w, _h = keeper.floor_of(zone)
            laid = {"floor": [{"kind": "lamp", "x": left + 2, "y": top + 1, "name": "A talk invitation", "picture": "p9"}],
                    "wall": [], "under": [], "on": [], "hang": []}
            self.assertTrue(keeper._lay_room(world, zone, laid, "2026-11-19", "", ""))
            lamp = next(one for one in world["items"] if one.get("shot_of") == "p9")
            self.assertEqual((lamp["kind"], lamp.get("picture")), ("lamp", ""), "drawn as itself, not as the picture")
            self.assertTrue((Path(folder) / "world" / "pictures" / lamp["shot"]).is_file())
            keeper.save(world)
            whole, kind = keeper.original(f"shot-{lamp['id']}")
            self.assertEqual((whole, kind), (shot.read_bytes(), "image/png"), "the original, whole")
            self.assertIsNone(keeper.original("shot-i99999"))
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn('shown.addEventListener("click", () => sideLarger(item.original_url || real));', page)
        self.assertIn("const small = play.floorPixels.get(pixelKey) || playFinePixel(image,", page, "a floor's picture in pixels too (kept once made)")
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('one.pop(hidden, None)', routes, "where it is on their disk never sent")

    def test_a_things_picture_is_looked_for_on_the_web_theirs_one_source_among_it(self) -> None:
        # 2026-11-19: "程序不一定要看已有截图，而是要去找自己觉得合适的（比如程序自己去找那个论文）"; "后面有截图的，模型觉得截图
        # 不够好也可以去自己搜索"; "模型主要rely on 自己搜，只是用户截图也是搜的一个source".
        from unittest import mock
        from PIL import Image
        from personalized_agent.agent import playfind
        # 2026-11-20: "这个材料包的照片明显不是我的"; "让他支持找文件"; "不一定要一个途径".
        self.assertIn("THE THING ITSELF, NEVER ONE LIKE IT", playfind.FIND_SYSTEM)
        self.assertIn("SEARCH THE WEB, with several searches", playfind.FIND_SYSTEM)
        self.assertIn("GIVE UP TO 4 CHOICES", playfind.FIND_SYSTEM)
        self.assertIn('"find_picture": "<THE THING ITSELF', muse.ROOM_SYSTEM)
        self.assertEqual(muse.read_room({"legend": {"A": {"kind": "lamp", "find_picture": "the paper's first page"}},
                                         "floor": ["A.."]}, ["..."], 0, 0, lambda k: (1, 1), set(), 0)["floor"][0]
                         ["find_picture"], "the paper's first page")
        with tempfile.TemporaryDirectory() as folder:
            shot = Path(folder) / "theirs.png"
            Image.new("RGB", (120, 80), "#336699").save(shot)
            keeper = pw.PlayWorld(Path(folder) / "world", ByTask({}), CONFIG)
            world = keeper.world()
            world["zones"][0]["task"] = "t1"
            zone = world["zones"][0]
            lamp, bed, window = world["items"][0], world["items"][1], world["items"][2]
            keeper._pictures_by_task = {"t1": [{"id": "p1", "path": str(shot), "what": "their draft", "when": ""}]}
            keeper._pictures_anywhere, keeper._sources_by_task = [], {"t1": []}
            keeper._ask_to_find(lamp, zone, "the draft they wrote")
            keeper._ask_to_find(bed, zone, "Attention Is All You Need, first page")
            keeper._ask_to_find(window, zone, "a view nobody has")           # nothing found: tried again later
            keeper.save(world)

            def from_web(url, out, config=None, allowed=None):
                page = Image.new("RGB", (200, 260), "#ffffff")         # a page with its lines: unlike their flat one
                from PIL import ImageDraw
                for row in range(20, 240, 24):
                    ImageDraw.Draw(page).rectangle((16, row, 120 + row % 60, row + 8), fill="#222222")
                page.save(out)
                return True
            refused = lambda url, out, config=None, allowed=None: False          # noqa: E731
            answers = iter([[{"theirs": "p1"}],
                            [{"url": "https://refuses.example/cover.jpg", "what": "a cover", "allowed": lambda a: True},
                             {"url": "https://example.org/paper.pdf", "what": "its first page",
                              "allowed": lambda address: True}], []])
            tried = []

            def from_web_or_not(url, out, config=None, allowed=None):          # the first site refuses: the next
                tried.append(url)
                return refused(url, out) if "refuses" in url else from_web(url, out)
            with mock.patch.object(playfind, "find", side_effect=lambda *a, **k: next(answers)) as asked, \
                    mock.patch.object(playfind, "from_url", side_effect=from_web_or_not):
                self.assertEqual(keeper._find_pictures("2026-11-19"), 2)
            self.assertEqual(tried, ["https://refuses.example/cover.jpg", "https://example.org/paper.pdf"],
                             "each choice tried in turn till one is read")
            self.assertEqual(asked.call_args_list[0].args[3][0]["id"], "p1", "theirs given to the finder, among the web")
            items = {one["id"]: one for one in keeper.world()["items"]}
            self.assertEqual(items[lamp["id"]]["shot_of"], "p1", "theirs, chosen")
            self.assertEqual(items[bed["id"]]["shot_of"], f"found-{bed['id']}", "the web's, found")
            self.assertTrue(Path(items[bed["id"]]["shot_source"]).is_file(), "kept whole: its original")
            self.assertEqual(items[window["id"]].get("find_tries"), 1, "kept on the thing, tried again later")
            self.assertFalse(hasattr(pw.PlayWorld, "FIND_MOST"), "no limit: as many as are asked for")
            with mock.patch.object(playfind, "find", return_value=[]):
                for _ in range(5):
                    keeper._find_pictures("2026-11-19")
            self.assertEqual(keeper.world()["items"][2]["find_tries"], pw.PlayWorld.FIND_TRIES_MOST)

    def test_more_instruments_and_arrangements_the_same_on_the_page(self) -> None:
        # 2026-11-19: "你的音乐可选再加多".
        sound = (support.ROOT / "personalized_agent" / "api" / "web" / "play-sound.js").read_text(encoding="utf-8")
        listed = lambda name: re.findall(r'"([a-z]+)"', sound.split(f"const {name} = [", 1)[1].split("];", 1)[0])  # noqa: E731
        self.assertEqual(listed("PLAY_INSTRUMENTS"), list(pw.INSTRUMENTS))
        self.assertEqual(listed("PLAY_ARRANGEMENTS"), list(pw.ARRANGEMENTS))
        self.assertGreaterEqual((len(pw.INSTRUMENTS), len(pw.ARRANGEMENTS)), (21, 13))
        for name in pw.INSTRUMENTS[10:]:
            if name != "bass":
                self.assertIn(f'name === "{name}"', sound, f"{name} is drawn by the page")
        for style in pw.ARRANGEMENTS[5:]:
            self.assertIn(f'"{style}"', sound.split("function playSoundAhead", 1)[1], f"{style} is played")

    def test_the_world_is_laid_again_once_when_the_materials_tiles_are_all_in(self) -> None:
        # 2026-11-19: "刚刚又卡了" - laid again for each of 47 tiles as it came, ~300 ms each, the first seconds held.
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        body = page.split("function playMaterialTiles(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("play.chunks.clear()", body, "not laid again tile by tile")
        self.assertIn("if (!play.tilesLoading) playTilesLaidAgain();", body)

    def test_every_track_is_on_the_page_and_on_disk_credited(self) -> None:
        # 2026-11-19: "你找些别的cc0音乐吧"; "你怎么没有弦乐，小提琴那种".
        web = support.ROOT / "personalized_agent" / "api" / "web"
        sound = (web / "play-sound.js").read_text(encoding="utf-8")
        block = sound.split("const PLAY_TRACKS = {", 1)[1].split("};", 1)[0]
        files = dict(re.findall(r'"?([a-z-]+)"?: "([a-z-]+\.(?:ogg|mp3))"', block))
        self.assertEqual(set(files), set(pw.TRACKS))
        assets = (web / "play" / "ASSETS.txt").read_text(encoding="utf-8")
        for name, file in files.items():
            self.assertTrue((web / "play" / "music" / file).is_file(), file)
            self.assertIn(f"music/{file.split('.')[0]}" if file.startswith("calm-") else f"music/{file}", assets
                          if not file.startswith("calm-") else assets.replace("calm-*", file.split(".")[0]), file)
        self.assertGreaterEqual(len(pw.TRACKS), 20)
        self.assertTrue(any("violin" in mood for mood in pw.TRACKS.values()), "strings, a violin")

    def test_every_great_piece_is_drawn_a_room_may_have_one_of_any_size(self) -> None:
        # 2026-11-19: "agent 在play上的形象消失了" - a room laid a "piece" as a thing, tile by tile, without a size or a
        # design; the page could not draw it and the frame stopped before the walker. "你的大件要保证能画出来呀";
        # "室内也是允许大件的，和2*5的内饰冲突（冲突有size限制，大件没有）".
        from personalized_agent.agent import playpieces as pieces
        self.assertIsNone(pw.PlayWorld._room_fit("piece"), "never one of a room's things")
        self.assertIsNone(pw.PlayWorld._room_fit("piece", True))
        self.assertIn("A ROOM MAY HAVE ONE TOO, AS LARGE AS ITS FLOOR ALLOWS", pieces.room_pieces_note())
        rows = ["." * 14] * 10
        asked = pieces.read_asked({"pieces": [{"kit": "composite", "what": "An orrery.", "x": 0, "y": 0, "w": 7, "h": 6}]},
                                  rows, 0, 0, set(), 2)
        self.assertEqual([(a["w"], a["h"]) for a in asked], [(7, 6)], "in a room, larger than any thing may be")
        tower = {"shapes": [{"kind": "circle", "x": 0, "y": 0, "w": 3, "h": 3}], "name": "Orrery", "why": "x"}
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({pieces.TASK_PIECE: tower, pieces.TASK_PIECE_LOOK: {}}), CONFIG)
            world = keeper.world()
            zone = world["zones"][0]
            left, top, _w, _h = keeper.floor_of(zone)
            for n, (dx, dy) in enumerate([(0, 0), (1, 0), (2, 0), (0, 1), (0, 2)]):
                world["items"].append({"id": f"i{900 + n}", "zone": zone["id"], "kind": "piece", "x": left + dx,
                                       "y": top + dy, "name": "Orrery", "why": "Turning.", "picture": "", "designed": True})
            keeper.save(world)
            asked_now = []
            built = keeper._build_pieces
            keeper._build_pieces = lambda zid, ones, at: (asked_now.extend(ones), built(zid, ones, at))[1]
            self.assertEqual(keeper._finish_pieces("2026-11-19"), 1)
            self.assertEqual([(a["kit"], a["x"] - left, a["y"] - top, a["w"], a["h"]) for a in asked_now],
                             [("composite", 0, 0, 3, 3)], "its tiles taken together, designed as a piece is")
            left_over = [one for one in keeper.world()["items"] if one["kind"] == "piece"]
            self.assertEqual(len(left_over), 1)
            self.assertTrue(left_over[0]["spec"] and pw.item_size(left_over[0]) == (3, 3), "one piece, its size its own")
        with tempfile.TemporaryDirectory() as folder:          # never designed: tried a few times, then let go
            keeper = pw.PlayWorld(Path(folder), ByTask({}), CONFIG)
            world = keeper.world()
            zone = world["zones"][0]
            world["items"].append({"id": "i950", "zone": zone["id"], "kind": "piece", "x": 0, "y": 0, "name": "A"})
            keeper.save(world)
            keeper._build_pieces = lambda zid, ones, at: 0
            for _ in range(keeper.PIECE_TRIES_MOST):
                keeper._finish_pieces("2026-11-19")
            self.assertFalse([one for one in keeper.world()["items"] if one["id"] == "i950"])
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn("if (!item.spec || !(item.w > 0 && item.h > 0)) {", page, "not designed yet: its scaffold")
        self.assertIn('layer("signs", () => playSignsDraw(size));', page, "a layer that fails never stops the frame")
        self.assertIn("function playRideOverdue(now = performance.now()) {", page, "a ride never left on")
        self.assertIn("playRidingPiece(item)", page)

    def test_progress_widgets_change_with_the_work_and_the_page_draws_each_stage(self) -> None:
        # 2026-11-19: "里面支持养鸡场，植物种植之类，不同阶段会变化形态，作为控件给模型"; "再加点".
        from personalized_agent.agent import playwidgets as widgets
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        counts = dict(re.findall(r"\b([a-z_]+): (\d),", page[page.index("const PLAY_WIDGETS = {"):page.index("for (const crop of PLAY_CROPS)")]))
        for name, one in widgets.WIDGETS.items():
            if name.startswith("field_"):
                self.assertIn(f'"{one["crop"]}"', page[page.index("const PLAY_CROPS"):page.index("const PLAY_CROP_STAGES")], name)
                self.assertEqual(len(one["stages"]), 6)
                continue
            self.assertEqual(int(counts[name]), len(one["stages"]), f"{name}: as many stages on the page")
            w, h = one["size"]
            self.assertIn(f"{name}: [{w}, {h}]", page, f"{name}: the same size on the page")
        self.assertEqual(pw.PlayWorld._room_fit("widget:chicken_farm", True), (3, 2))
        self.assertIsNone(pw.PlayWorld._room_fit("widget:chicken_farm", False), "a farm is not indoors")
        self.assertEqual(pw.PlayWorld._room_fit("widget:bookshelf", False), (2, 2))
        self.assertIsNone(pw.PlayWorld._room_fit("widget:no_such", True))
        self.assertIn("widget:field_<crop> 3x2", widgets.widgets_told(True))
        self.assertNotIn("widget:pasture", widgets.widgets_told(False))
        self.assertIn("MILESTONES, A STAGE'S RESULT, ANYTHING SPECIAL ARE BEST SHOWN BY A WIDGET", muse.ROOM_SYSTEM)
        rows = ["......", "......", "..DD.."]
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({}), CONFIG)
            world = keeper.world()
            zone = world["zones"][0]
            left, top, _w, _h = keeper.floor_of(zone)
            laid = muse.read_room({"legend": {"F": {"kind": "widget:bookshelf", "card": "c1"}},
                                   "floor": ["FF....", "FF....", "..DD.."]}, rows, left, top,
                                  lambda k: pw.PlayWorld._room_fit(k, False), set(), 0)
            self.assertEqual(laid["floor"][0]["kind"], "widget:bookshelf")
            keeper._lay_room(world, zone, laid, "2026-11-19", "", "")
            made = next(one for one in world["items"] if one.get("kind") == "widget")
            self.assertEqual((made["widget"], pw.item_size(made)), ("bookshelf", (2, 2)))
        self.assertIn('if (item.kind === "widget") { playWidgetDraw(context, item, x, y); return; }', page)

    def test_a_great_grounds_halls_are_rooms_of_one_building_some_joined(self) -> None:
        # 2026-11-19: "融合之后不要独立房子"; "真正连通，允许有的连通有的不，像一个个房间".
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({}), CONFIG)
            world = keeper.world()
            halls = []
            for n, (task, x) in enumerate((("t1", 12), ("t2", 40), ("t3", 26))):
                zone = keeper._add_zone(world, "studio", f"Hall {n}", "Work.", "2026-10-01")
                zone.update(task=task, x=x, y=14 + n, w=8, h=5 + n, in_great="p1")
                halls.append(zone)
            garden = keeper._add_zone(world, "garden", "Yard", "", "2026-10-01")
            garden.update(task="t4", x=22, y=13, w=8, h=8, in_great="p1", shape={"kind": "blob", "seed": 1})
            world["zones"].insert(0, {"id": "zg", "kind": "estate", "plot": True, "great": "p1", "x": 10, "y": 9,
                                      "w": 60, "h": 40, "shape": {"kind": "rect"}})
            parts = [{"task": "t1", "open_to_next": True}, {"task": "t3", "open_to_next": False}, {"task": "t2"}]
            keeper._join_halls(world, "p1", (10, 10, 60, 40), parts)
            row = sorted(halls, key=lambda z: z["x"])
            self.assertEqual([z["task"] for z in row], ["t1", "t3", "t2"], "in the plan's order")
            self.assertEqual(len({(z["y"], z["h"]) for z in row}), 1, "one top, one depth: one building")
            for a, b in zip(row, row[1:]):
                self.assertEqual(b["x"], a["x"] + a["w"] + 1, "the next one's left wall this one's right")
            self.assertEqual([len(z["inner_doors"]) for z in row], [1, 0, 0], "a door where it opens to the next, none where not")
            self.assertEqual(row[0]["inner_doors"][0]["x"], row[1]["x"])
            taken = set()
            for z in row:
                x0, y0, x1, y1 = places.zone_rect(z)
                taken |= {(x, y) for x in range(x0, x1) for y in range(y0, y1)}
            fx, fy, fw, fh = keeper.floor_of(garden)
            self.assertFalse({(x, y) for x in range(fx, fx + fw) for y in range(fy, fy + fh)} & taken,
                             "an open part the building stands on moved to free ground")
        regions_js = (support.ROOT / "personalized_agent" / "api" / "web" / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("for (const way of zone.inner_doors || []) {", regions_js)

    def test_the_world_then_each_month_asked_once_and_shown_at_each_tasks_place(self) -> None:
        # 2026-11-19: "每个task ... 也可以加上在你做这个project的时候，世界上发生了什么大事的content"; "也可以是作为element放在房间里".
        said = {"2026-09": [{"date": "2026-09-20", "headline": "A probe reaches a far moon", "what": "It landed.",
                             "kind": "space", "url": "https://www.reuters.com/science/probe"},
                            {"date": "2026-09-21", "headline": "Made up, no source", "url": ""},
                            {"date": "2026-08-30", "headline": "Not this month", "url": "https://www.bbc.com/news/x"}],
                "2026-10": [{"date": "2026-10-02", "headline": "A great match", "kind": "sport",
                             "url": "https://apnews.com/article/match"},
                            {"date": "2026-10-03", "headline": "Not mainstream", "url": "https://news.example.org/y"}],
                "1999-01": [{"date": "1999-01-01", "headline": "Not asked", "url": "https://www.reuters.com/old"}]}
        read = muse.read_world_events(said, ["2026-09", "2026-10"])
        self.assertEqual([e["headline"] for e in read["2026-09"]], ["A probe reaches a far moon"], "a source, its month")
        self.assertNotIn("1999-01", read)
        # 2026-11-19: "要求用Mainstream的新闻" - a page not a mainstream news organisation's let go.
        self.assertEqual([e["headline"] for e in read["2026-10"]], ["A great match"])
        self.assertFalse(muse.mainstream("https://reuters.com.evil.io/x"))
        self.assertEqual(muse.months_between("2026-11-20", "2027-02-01"), ["2026-11", "2026-12", "2027-01", "2027-02"])
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), ByTask({muse.TASK_WORLD_EVENTS: said}), CONFIG)
            world = keeper.world()
            zone = keeper._add_zone(world, "study", "Hive census", "Counting bees.", "2026-11-08")
            zone.update(task="t1", days=["2026-09-18", "2026-10-05"])
            keeper.save(world)
            self.assertEqual(keeper._world_events("2026-11-19"), 2)
            asked = [r for r in keeper.models.asked if r.task == muse.TASK_WORLD_EVENTS]
            self.assertEqual(len(asked), 1)
            self.assertTrue(asked[0].builtin_web, "searched on the web")
            self.assertEqual(json.loads(asked[0].messages[0]["content"]), {"months": ["2026-10", "2026-09"]},
                             "only the months - nothing of the user")
            book = keeper.world()["world_events"]
            self.assertTrue(book["2026-09"]["whole"])
            keeper._world_events("2026-11-20")
            self.assertEqual(len([r for r in keeper.models.asked if r.task == muse.TASK_WORLD_EVENTS]), 1, "asked once")
            world = keeper.world()
            self.assertEqual(keeper._place_news(world, "2026-11-19"), 1, "its design chose none: one laid")
            shown = [one for one in world["items"] if one.get("news")]
            self.assertEqual([e["headline"] for e in shown[0]["news"]], ["A probe reaches a far moon", "A great match"])
            self.assertIn("A great match", shown[0]["news_text"])
            self.assertIn("news_text", pw.PlayWorld._words_of(shown[0]), "put into the language shown with the rest")
            self.assertEqual(keeper._place_news(world, "2026-11-19"), 0, "once")
        self.assertIn('"the_world_then": what happened in the world while this work was done', muse.ROOM_SYSTEM)
        laid = muse.read_room({"legend": {"N": {"kind": "chair", "news": True}}, "floor": ["N..."]}, ["...."], 0, 0,
                              pw.PlayWorld._room_size, set(), 4)
        self.assertTrue(laid["floor"][0]["news"], "the thing the design chose to show it")
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn("${playNewsCard(item)}", page)
        # 2026-11-19: "你play翻译成中文之后，你的那些按钮(比如music) 也要变成中文"
        self.assertIn('data-play-label="Sound" aria-label="${escapeHtml(playWords("Sound"))}"', page)
        self.assertIn("function playRelabel() {", page)
        i18n = (support.ROOT / "personalized_agent" / "api" / "web" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn("textarea, pre, code, .side-play\";", i18n, "the page's translation leaves Play to its own")

    def test_the_theme_first_then_a_scene_each_resting_till_its_theme_has_come_round(self) -> None:
        # 2026-11-19: "model先选主题，再选场景，然后一个主题里的场景用过就停用，直到主题里所有场景都被用过再启用"
        every = [s for _what, scenes in muse.SCENE_THEMES.values() for s in scenes]
        self.assertEqual(sorted(every), sorted(muse.MOOD_SCENES), "every scene in exactly one theme")
        self.assertEqual(len(every), len(set(every)))
        # 2026-11-20: space, the sea and the sky are themes of their own ("要加水下/海底类的主题场景，和天空/云端的主题场景，
        # 还有太空/宇宙的主题场景").
        far = list(muse.SCENE_THEMES["cosmos"][1])
        self.assertNotIn("starry_space", muse.scenes_open(["starry_space"])["cosmos"])
        self.assertIn("starry_space", muse.scenes_open(["starry_space"], keep="starry_space")["cosmos"], "the land it is in")
        self.assertEqual(muse.scenes_open(far)["cosmos"], far, "all used: the theme comes round again")
        self.assertEqual(muse.scenes_open(far + ["moon_base"])["cosmos"], [s for s in far if s != "moon_base"])
        for theme in ("undersea", "sky", "cosmos"):
            self.assertGreaterEqual(len(muse.SCENE_THEMES[theme][1]), 5, theme)
            for scene in muse.SCENE_THEMES[theme][1]:
                self.assertIn(scene, muse.SCENE_OWN, f"{scene}: its own houses and things")
        open_now = muse.scenes_open(["quiet_grove"])
        system = muse.mood_scene_system(open_now=open_now)
        self.assertIn("THEME woods", system)
        self.assertNotIn("- quiet_grove:", system, "resting: not offered")
        self.assertIn('{"theme": "<the theme\'s name>", "scene":', system)
        moved = muse.read_mood({"scene": "quiet_grove"}, "calm", open_now)
        self.assertEqual((moved["scene"], moved["theme"]), (open_now["woods"][0], "woods"), "a resting one: its theme's next")

    def test_vehicles_heard_coming_and_going_and_each_animal_its_own_cry(self) -> None:
        # 2026-11-19: "交通工具驶过（飞机，火车，轮船等）要有由远及近的音效，动物也是要有被追赶的音效（不同动物不一样）"
        web = support.ROOT / "personalized_agent" / "api" / "web"
        sound = (web / "play-sound.js").read_text(encoding="utf-8")
        self.assertIn("playSoundPassing();                                     // what goes by", sound)
        for look in ("train", "tram", "sleigh", "caravan", "coaster", "mine_cart", "ferry", "sailboat", "airship", "plane"):
            self.assertIn(f"  {look}: {{ rumble:", sound, look)
        self.assertIn("const k = Math.max(0.75, Math.min(1.3, PLAY_SOUND_SPEED / (PLAY_SOUND_SPEED -", sound, "pitch bent")
        animals = set()
        for _scenes, kinds in muse.SCENE_LIFE.values():
            animals |= set(kinds)
        for name in animals:
            self.assertTrue(re.search(rf'(\n  {name}: \[|\["{name}", ")', sound), f"{name} has a cry of its own")
        act = (web / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("playSoundCry(one.name, one.x - play.me.x,", act)

    def test_the_language_button_shows_the_language_and_opens_a_panel(self) -> None:
        # 2026-11-19: "点一下出来一个panel, 显示当前语言是什么并且切换。那个符号显示的就是当前语言"
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn('closest("[data-play-language]")) playLanguagePanel(box);', page)
        self.assertIn('const PLAY_LANGUAGES = [["en", "EN", "English"], ["zh", "中", "中文"]];', page)
        self.assertIn("playLanguageSwitch(to.dataset.playLanguageTo);", page)
        self.assertIn('${code === now ? "✓" : ""}', page, "the one chosen ticked")
        self.assertNotIn("side-play-language-now", page, "no line saying it again")
        # 2026-11-19: "小地图上白框显示这里是黑的" - a strait laid between two lands out past them both, to no end.
        regions = (support.ROOT / "personalized_agent" / "api" / "web" / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("const strait = d - landReach <= PLAY_SEA_MOST && gap / 2 < straitHalf", regions)
        self.assertIn("const pad = PLAY_LAND_MOST + 2, R = PLAY_MINI_PX;", page)
        # 2026-11-20: kept beside the world, its version raised by hand only - never the code's hash.
        self.assertIn('if (said && String(said.v) === playMiniKeptVersion()) {', page, "kept pictures of the drawing that made them")
        # 2026-11-19: "stuck那个改成在view的中心（或者附近，如果中心不能站）刷新"
        self.assertIn('closest("[data-play-unstuck]")) playUnstuck(true);', page)
        self.assertIn("const middle = toView && play.lookAt ? play.lookAt : [play.me.x, play.me.y];", page)

    def test_the_page_shows_the_world_in_the_language_chosen(self) -> None:
        page = (support.ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertIn('return /^zh/i.test(document.documentElement.lang || "") ? "zh" : "en";', page)
        self.assertIn("play.world = playInLanguage(said.world, language);", page)
        self.assertIn('attributeFilter: ["lang"]', page, "chosen again in settings: shown again in it")
        # 2026-11-19: "你现在这个中英文切换的setting好像没做呀": the language saved, the side panel told and drawn again in it.
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('app.side_events.tell("language", language=', routes)
        notes = (support.ROOT / "personalized_agent" / "api" / "web" / "notes.js").read_text(encoding="utf-8")
        self.assertIn('line.addEventListener("language", () => {', notes)
        self.assertIn('sessionStorage.setItem("playOpenAgain", "1");', notes)
        from personalized_agent.common import modules
        self.assertEqual(modules.module_of(pw.TASK_TRANSLATE), "play_world")
        for task in (muse.TASK_MOOD, muse.TASK_MUSE, muse.TASK_ROOM, muse.TASK_ROOM_LOOK):
            self.assertEqual(modules.module_of(task), "play_world", task)


class OnTheTableAndHungFromAboveTests(unittest.TestCase):
    def test_the_page_draws_them_in_their_layers_and_walks_under_them(self) -> None:
        web = support.ROOT / "personalized_agent" / "api" / "web"
        page = (web / "play.js").read_text(encoding="utf-8")
        self.assertIn("return item.hang ? 2 : item.on ? 1 : 0;", page)
        self.assertIn("function playMuseCard(zone)", page)
        regions_js = (web / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("PLAY_UNDER_KINDS.has(item.kind) || item.on || item.hang) continue;", regions_js)


if __name__ == "__main__":
    unittest.main()

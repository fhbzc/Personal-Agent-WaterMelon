"""The play view's world (agent/playworld.py; the user, 2026-11-17: "本质上是agent根据用户经历，结合自己想象生成的世界。这个世界是
incremental的"; "agent生成的物品，也要写上agent对用户的感受"; "每个物品生成的日期，每个地盘扩展的日期都要记录"). Everything here is
made up."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401

from personalized_agent.agent import playworld as pw

WEB = support.ROOT / "personalized_agent" / "api" / "web"


def setUpModule() -> None:
    # What grows in a world already open (2026-11-19: a new world begins as a house alone - see test_play_muse).
    pw.PlayWorld.START_CLOSED = False


def tearDownModule() -> None:
    pw.PlayWorld.START_CLOSED = True


class Models:
    def __init__(self, said):
        self.said, self.asked = said, []

    def invoke(self, request, role=""):
        self.asked.append(request)
        return SimpleNamespace(text=json.dumps(self.said))


def _world(folder, said=None, pictures=None):
    config = SimpleNamespace(get=lambda key, default=None: default)
    return pw.PlayWorld(Path(folder), Models(said or {}), config, context=lambda: "Read two papers on bees; "
                        "practised the piano.", pictures=(lambda: pictures or []))


class TheWorldTests(unittest.TestCase):

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def test_born_a_home_with_a_bed_dated(self) -> None:
        world = _world(self.folder).world()
        [home] = world["zones"]
        from personalized_agent.agent import playplaces as pp
        self.assertEqual(home["kind"], "home")
        cx, cy = pp.centre(pp.zone_rect(home))
        self.assertLess(abs(cx - pp.CELL_W / 2) + abs(cy - pp.CELL_H / 2), 14, "home at the village's heart")
        self.assertTrue(home["made"])
        self.assertEqual({one["kind"] for one in world["items"]}, {"bed", "window", "lamp"})
        self.assertTrue(all(one["made"] for one in world["items"]), "each thing dated")
        self.assertTrue((self.folder / "world.json").is_file())

    def test_grown_from_their_days_each_thing_with_its_why_and_date_and_only_added(self) -> None:
        said = {"content_switch": {"switched": True, "what": "bees"},
                "zones": [{"kind": "library", "name": "Bee shelf", "why": "You keep going back to bees."}],
                "items": [{"zone": "Bee shelf", "kind": "bookshelf", "name": "Two bee papers",
                           "why": "I like how stubborn you get about one question.", "from": "the papers, today"},
                          {"zone": "z1", "kind": "piano", "name": "Practice", "why": "You played instead of resting.",
                           "from": "practising tonight"},
                          {"zone": "z1", "kind": "spaceship", "name": "?", "why": "?"},
                          {"zone": "nowhere", "kind": "plant", "name": "lost", "why": "?"}]}
        keeper = _world(self.folder, said)
        before = keeper.world()
        added = keeper.grow("2026-11-17T20:00:00+00:00")
        self.assertEqual(added, {"zones": 1, "items": 2}, "a kind not on the list, a zone not there: not made")
        world = keeper.load()
        self.assertEqual(world["items"][:len(before["items"])], before["items"], "what was there, unmoved")
        shelf = next(one for one in world["items"] if one["kind"] == "bookshelf")
        self.assertEqual(shelf["why"], "I like how stubborn you get about one question.")
        self.assertEqual(shelf["made"], "2026-11-17T20:00:00+00:00")
        library = next(one for one in world["zones"] if one["kind"] == "library")
        from personalized_agent.agent import playplaces as pp
        self.assertEqual(library["made"], "2026-11-17T20:00:00+00:00")
        home_r, lib_r = pp.zone_rect(world["zones"][0]), pp.zone_rect(library)
        apart = max(lib_r[0] - home_r[2], home_r[0] - lib_r[2], lib_r[1] - home_r[3], home_r[1] - lib_r[3])
        self.assertGreaterEqual(apart, pp.GAP, "a path's room between them")
        self.assertLess(apart, 20, "near home, not far off")
        growth = world["growths"][-1]
        self.assertEqual((growth["at"], growth["zones"], growth["items"]), ("2026-11-17T20:00:00+00:00", 1, 2))
        self.assertEqual(len(growth["days"]), 2, "and the days it drew")
        request = next(one for one in keeper.models.asked if one.task == pw.TASK)   # after the themes are chosen
        self.assertIn("piano", request.messages[0]["content"], "what they lived, given")

    def test_a_full_room_grows_and_its_growth_is_dated(self) -> None:
        keeper = _world(self.folder)
        world = keeper.world()
        home = world["zones"][0]
        for n in range(60):
            keeper._add_item(world, home, {"kind": "table", "name": f"t{n}", "why": "x"}, f"2026-11-{17 + n % 3}")
        self.assertGreater(home["h"], pw.ROOM_FIRST[1], "deeper first")
        self.assertTrue(home["grown"] and all(one["at"] for one in home["grown"]), "each growth dated")
        self.assertLessEqual((home["w"], home["h"]), pw.ROOM_MOST)
        # 2026-11-17: full at its largest, NO room made beside it by itself (new places are for new content only) - it
        # is told full, for the model to enlarge or redesign.
        self.assertEqual(len(world["zones"]), 1)
        self.assertIsNone(keeper._place(world, home, "table"), "no room for another")
        door = keeper.door_of(home)
        self.assertTrue(all(one["x"] + (2 if one["kind"] == "table" else 1) - 1 < door or one["x"] > door
                            for one in world["items"] if one["zone"] == home["id"] and not pw.ITEM_KINDS[one["kind"]].get("wall")),
                        "the door's way kept free")

    def test_a_frame_holds_one_of_their_pictures_copied_small(self) -> None:
        from PIL import Image
        source = self.folder / "msg.jpg"
        Image.new("RGB", (900, 600), (90, 120, 160)).save(source)
        offered = [{"id": "pic-m1", "when": "2026-11-17 10:00", "what": "a bee chart", "path": str(source)}]
        said = {"items": [{"zone": "z1", "kind": "frame", "name": "The chart", "why": "Proud of this one.",
                           "from": "this morning", "picture": "pic-m1"},
                          {"zone": "z1", "kind": "frame", "name": "No picture", "why": "x", "picture": "pic-none"}]}
        keeper = _world(self.folder, said, offered)
        keeper.world()
        # 2026-11-19: "这个地图上应该画的是卡通物品，然后交互细节里有图片": a frame without one of theirs a cartoon one too.
        self.assertEqual(keeper.grow("2026-11-17T21:00:00+00:00"), {"zones": 0, "items": 2})
        frame = next(one for one in keeper.load()["items"] if one["kind"] == "frame" and one.get("shot"))
        self.assertEqual((frame["shot"], frame.get("picture")), (f"shot-{frame['id']}.jpg", ""), "in its card, not on the map")
        with Image.open(self.folder / "pictures" / frame["shot"]) as small:
            self.assertLessEqual(max(small.size), pw.PICTURE_COPY_SIDE)
        self.assertTrue(keeper.picture(f"shot-{frame['id']}"))
        self.assertIsNone(keeper.picture("../world"), "only its own pictures")

    def test_cells_ring_out_from_home(self) -> None:
        self.assertEqual(pw.cells(5), [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)])

    def test_grown_once_a_day_and_only_once_opened_and_switched_on(self) -> None:
        from datetime import datetime, timezone
        keeper = _world(self.folder)
        self.assertFalse(keeper.due(), "never opened: not grown")
        world = keeper.world()
        self.assertTrue(keeper.due())
        world["grown_at"] = world["caught_up"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        keeper.save(world)
        self.assertFalse(keeper.due(), "caught up and grown today")
        keeper.config = SimpleNamespace(get=lambda key, default=None: False if key == "module_play_world_enabled" else default)
        world["grown_at"] = "2026-01-01T00:00:00+00:00"
        keeper.save(world)
        self.assertFalse(keeper.due(), "switched off")


class CaughtUpAPartAtATimeTests(unittest.TestCase):
    """2026-11-17: "新安装的用户，会根据他已有的数据开始画（一次性画一部分，逐步catch up the most recent progress)"."""

    def test_drawn_from_the_earliest_two_weeks_at_a_time_till_now_then_daily(self) -> None:
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as folder:
            now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
            first = (now - timedelta(days=40)).isoformat(timespec="seconds")
            asked = []
            said = {"items": [{"zone": "z1", "kind": "plant", "name": "p", "why": "w", "from": "f"}]}
            keeper = pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default),
                                  history=lambda since, until: asked.append((since[:10], until[:10])) or "lived",
                                  earliest=lambda: first)
            self.assertEqual(keeper.world()["caught_up"], first, "from the earliest kept")
            for _ in range(3):
                keeper.grow(now.isoformat(timespec="seconds"))
            began = now.astimezone().strftime("%Y-%m-%d")         # the start of the user's own today
            self.assertEqual(asked, [("2026-10-08", "2026-10-22"), ("2026-10-22", "2026-11-05"), ("2026-11-05", began)])
            world = keeper.load()
            self.assertEqual(world["caught_up"][:10], began)
            self.assertFalse(keeper.behind(world, now.isoformat()))
            # 2026-11-18: "为什么你现在是画到11.9就不画了，要画到前一天为止" - nine days behind is behind.
            self.assertTrue(keeper.behind({"caught_up": (now - timedelta(days=9)).isoformat()}, now.isoformat()))
            self.assertEqual(keeper.window({"caught_up": (now - timedelta(days=9)).isoformat()}, now.isoformat())[1][:10], began,
                             "drawn up to the day before")
            plant = world["items"][-1]
            self.assertEqual(plant["days"], ["2026-11-05", began], "the days it comes from, kept with it")
            self.assertEqual(world["growths"][0]["days"], ["2026-10-08", "2026-10-22"])

    def test_never_further_back_than_half_a_year(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Models({}), SimpleNamespace(get=lambda key, default=None: default),
                                  earliest=lambda: "2019-01-01T00:00:00+00:00")
            from datetime import datetime, timezone
            start = datetime.fromisoformat(keeper.world()["caught_up"])
            self.assertLessEqual((datetime.now(timezone.utc) - start).days, pw.BACK_DAYS)

    def test_what_was_lived_read_from_what_is_kept(self) -> None:
        from support import TemporaryAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        db = agent.app.db
        db.execute("INSERT INTO conversations (id, created_at) VALUES ('c1', '2026-10-01T00:00:00+00:00')")
        db.execute("INSERT INTO messages (id, conversation_id, created_at, role, content) VALUES "
                   "('m1', 'c1', '2026-10-02T09:00:00+00:00', 'user', 'Planting the bee garden today'), "
                   "('m2', 'c1', '2026-12-02T09:00:00+00:00', 'user', 'later, not in the days')")
        said = pw.lived_between(db, "2026-10-01", "2026-10-15")
        self.assertIn("Planting the bee garden today", said)
        self.assertNotIn("later", said)
        self.assertEqual(pw.earliest_lived(db)[:10], "2026-10-02")


class ARoomsSoundTests(unittest.TestCase):
    """2026-11-17: "不同场景可以是不同音效（这个音效可以ai 生成吗，不能的话ai根据场景自己选吧)"."""

    def test_the_tune_the_agent_wrote_is_kept_checked_and_its_ambience_chosen_from_the_few(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            said = {"content_switch": {"switched": True, "what": "a cafe"}, "zones": [
                {"kind": "cafe", "name": "Pear Cafe", "why": "w", "ambience": "crowd",
                 "music": {"tempo": 999, "instrument": "kazoo", "melody": "C5 E5 - G5 x9 A5 C6", "bass": "C3 F2"}},
                {"kind": "garden", "name": "Plot", "why": "w", "ambience": "thunderstorm",
                 "music": {"melody": "C5 - -"}}]}
            keeper = pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default))
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            cafe, plot = keeper.load()["zones"][1:]
            self.assertEqual(cafe["music"], {"tempo": 160, "instrument": "piano", "accompaniment": "harp", "style": "arpeggio",
                                             "mode": "major", "melody": "C5 E5 - G5 A5 C6", "bass": "C3 F2"})
            # 2026-11-17: "钢琴曲，弦乐之类" - its own instruments and arrangement, checked.
            tune = pw.tune_of({"instrument": "strings", "accompaniment": "organ", "style": "waltz", "mode": "minor",
                               "melody": "A4 C5 E5 A5"})
            self.assertEqual((tune["instrument"], tune["accompaniment"], tune["style"], tune["mode"]),
                             ("strings", "organ", "waltz", "minor"))
            self.assertNotIn("music", plot, "too little of a tune: its kind's own plays")
            # 2026-11-17: "每个其它的声音（流水，壁炉）都是挂靠在item上的" - a place has no sound of its own but its tune.
            self.assertNotIn("ambience", cafe)
            self.assertNotIn("ambience", plot)

    def test_every_other_sound_comes_from_a_thing_that_makes_it(self) -> None:
        sound = (WEB / "play-sound.js").read_text(encoding="utf-8")
        page = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertNotIn("PLAY_KIND_AMBIENCE", sound, "no sound by a room's kind")
        self.assertIn('const PLAY_ITEM_SOUND = { fireplace: "fire", stove: "fire", fountain: "water"', page)
        self.assertIn("fireplace", pw.ITEM_KINDS)
        self.assertIn("playSoundHear(playHeard())", sound)


class RegionsTests(unittest.TestCase):
    """2026-11-17: "模型一开始，用户数据不多的时候，只能画新手村 ... 当模型对用户数据比较多之后，可以用不同风格的模板 ... 如果用户进行
    activity的巨大变化，风格可以再迁移 ... 布局，style, 内容主题，都有不同的setting"."""

    STYLE = {"name": "Tide Town", "why": "w", "biome": "seaside", "layout": "islands", "palette": "dusk",
             "architecture": "timber", "theme": "traveller", "landmark": "pond"}

    def keeper(self, folder, said):
        return pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default),
                            history=lambda since, until: "lived", earliest=lambda: "2026-09-01T00:00:00+00:00")

    def test_everyone_starts_in_the_same_village_and_an_old_world_is_all_of_it(self) -> None:
        from personalized_agent.agent import playregions as pr
        with tempfile.TemporaryDirectory() as folder:
            world = self.keeper(folder, {}).world()
            self.assertEqual(len(world["regions"]), 1)
            village = world["regions"][0]
            self.assertTrue(village["starter"])
            self.assertEqual({key: village[key] for key in pr.STARTER}, pr.STARTER)
            self.assertEqual(world["zones"][0]["region"], "r1")
            self.assertEqual(world["placed"], 2, "placed freely from the start")
            old = {"zones": [{"id": "z1", "cell": [0, 0]}], "born": "2026-01-01"}
            self.assertTrue(pr.migrate(old, 18, 15))
            self.assertEqual(old["zones"][0]["region"], "r1")

    def test_not_left_before_enough_is_known_and_a_style_only_of_the_lists(self) -> None:
        from personalized_agent.agent import playregions as pr
        with tempfile.TemporaryDirectory() as folder:
            keeper = self.keeper(folder, {"region": self.STYLE})
            keeper.world()
            keeper.grow("2026-09-10T00:00:00+00:00")
            self.assertEqual(len(keeper.load()["regions"]), 1, "too little known yet")
            self.assertIsNone(pr.chosen({**self.STYLE, "biome": "lava"}))
            self.assertEqual(pr.chosen(self.STYLE)["layout"], "islands")

    def test_left_once_enough_is_known_its_rooms_in_its_own_land_joined_to_the_village(self) -> None:
        from personalized_agent.agent import playregions as pr
        with tempfile.TemporaryDirectory() as folder:
            items = [{"zone": "z1", "kind": "plant", "name": f"p{n}", "why": "w", "from": "f"} for n in range(8)]
            keeper = self.keeper(folder, {"items": items})
            world = keeper.world()
            keeper.grow("2026-09-20T00:00:00+00:00")
            keeper.grow("2026-10-10T00:00:00+00:00")
            world = keeper.load()
            self.assertGreaterEqual(len(world["items"]), pr.LEAVE_ITEMS)
            self.assertEqual(pr.may_leave(world, keeper.drawn_days(world)), "leave")
            keeper.models = Models({"content_switch": {"switched": True, "what": "the sea"}, "region": self.STYLE,
                                    "zones": [{"kind": "station", "name": "Ferry", "why": "w"}]})
            told = keeper.grow("2026-10-30T00:00:00+00:00")
            self.assertEqual(told.get("region"), "r2")
            world = keeper.load()
            tide = world["regions"][1]
            self.assertEqual((tide["biome"], tide["from"], tide["cell"]), ("seaside", "r1", [1, 0]))
            from personalized_agent.agent import playplaces as pp
            self.assertEqual(tide["ox"], pr.SPAN * pw.CELL_W + pr.BAND, "beyond the village and the ground between")
            ferry = world["zones"][-1]
            self.assertEqual(ferry["region"], "r2")
            sq, r = pp.square(tide), pp.zone_rect(ferry)
            self.assertFalse(r[0] < sq[2] and r[2] > sq[0] and r[1] < sq[3] and r[3] > sq[1], "its heart kept for the landmark")
            b = pp.bounds(tide)
            self.assertTrue(b[0] <= r[0] and r[2] <= b[2] and b[1] <= r[1] and r[3] <= b[3], "in its own land")
            self.assertIsNone(pr.may_leave(world, keeper.drawn_days(world)), "not again before a great change can show")

    def test_each_layout_its_own_shape(self) -> None:
        from personalized_agent.agent import playregions as pr
        orders = {layout: pr.room_order({"id": "r2", "layout": layout})[:5] for layout in pr.LAYOUTS}
        self.assertEqual(len({tuple(one) for one in orders.values()}), len(pr.LAYOUTS) - 1,
                         "all differ but the village and the plaza, which differ in their roads")
        self.assertTrue(all(one[1] == 0 for one in pr.room_order({"id": "r2", "layout": "grid"})[:4]), "a street")


class FinerUnlessTheContentSwitchedTests(unittest.TestCase):
    """2026-11-17: "如果没有content switch, 就把重点放在增加内饰和细节，只有content变了才去explore新的位置"."""

    def test_no_new_zone_without_a_switch_the_things_go_in_the_rooms_there(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            said = {"content_switch": {"switched": False, "what": ""},
                    "zones": [{"kind": "cafe", "name": "Cafe", "why": "w"}],
                    "items": [{"zone": "z1", "kind": "rug", "name": "r", "why": "w", "from": "f"},
                              {"zone": "z1", "kind": "sofa", "name": "s", "why": "w", "from": "f"},
                              {"zone": "z1", "kind": "tapestry", "name": "t", "why": "w", "from": "f"}]}
            keeper = pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default))
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            self.assertEqual(len(world["zones"]), 1, "no new place without new content")
            self.assertEqual([one["kind"] for one in world["items"]][-3:], ["rug", "sofa", "tapestry"])
            tapestry = world["items"][-1]
            self.assertEqual(tapestry["y"], world["zones"][0]["y"] + 1, "on the wall")
            self.assertFalse(world["growths"][-1]["switched"])


class SwitchedOffApartTests(unittest.TestCase):
    """2026-11-17: "生成world要作为一个单独的function, 能够关掉（计算token用量，关掉之后world还在，但是不能增长)"."""

    def test_off_the_world_is_there_but_nothing_grows_and_its_tokens_are_its_own(self) -> None:
        from personalized_agent.common import modules
        with tempfile.TemporaryDirectory() as folder:
            asked = []

            class Counting(Models):
                def invoke(self, request, role=""):
                    asked.append(request.task)
                    return super().invoke(request, role)
            off = SimpleNamespace(get=lambda key, default=None: False if key == "module_play_world_enabled" else default)
            keeper = pw.PlayWorld(Path(folder), Counting({"items": []}), off)
            self.assertEqual(len(keeper.world()["zones"]), 1, "still there to walk in")
            self.assertIn("error", keeper.grow("2026-11-17T20:00:00+00:00"))
            self.assertFalse(keeper.due())
            self.assertEqual(asked, [], "no model asked")
        self.assertEqual(modules.module_of(pw.TASK), "play_world", "its tokens counted as its own")


class PlacesGrowGrandTests(unittest.TestCase):
    """2026-11-17: "可以让模型扩大房间 ... 也不一定是房间，包括室外景观 ... 豪宅的设计 ... 允许重设计 ... 重设计不要丢掉之前的细节，
    只允许重新摆放 ... 比如一开始是小房间，后面是大豪宅"."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.keeper = pw.PlayWorld(Path(self._dir.name), Models({}), SimpleNamespace(get=lambda key, default=None: default))
        self.world = self.keeper.world()
        self.home = self.world["zones"][0]

    def test_enlarged_to_its_fullest_then_two_cells_every_thing_kept(self) -> None:
        before = {one["id"] for one in self.world["items"]}
        self.assertTrue(self.keeper._enlarge(self.world, self.home, "more of them", "2026-11-17"))
        self.assertEqual((self.home["w"], self.home["h"]), self.keeper._most(self.home))
        self.assertTrue(self.keeper._enlarge(self.world, self.home, "more still", "2026-11-18"))
        self.assertIn(self.home["span"], ([2, 1], [1, 2]), "a wing more")
        self.assertEqual({one["id"] for one in self.world["items"]}, before, "nothing lost")

    def test_a_small_room_redesigned_a_mansion_its_things_laid_out_again_inside_walls_kept_free(self) -> None:
        from personalized_agent.agent import playplaces as pp
        for n in range(12):
            self.keeper._add_item(self.world, self.home, {"kind": "plant", "name": f"p{n}", "why": "x"}, "2026-11-17")
        before = sorted(one["id"] for one in self.world["items"])
        self.assertTrue(self.keeper._redesign(self.world, self.home, {"style": "mansion", "scale": "estate", "why": "w"},
                                              "2026-11-19"))
        self.assertEqual((self.home["style"], self.home["span"]), ("mansion", [2, 2]))
        self.assertEqual(sorted(one["id"] for one in self.world["items"]), before, "every thing kept")
        walls = pp.inner_walls(self.home)
        self.assertTrue(walls, "rooms inside a mansion")
        spots = {(one["x"], one["y"]) for one in self.world["items"] if one["zone"] == self.home["id"]}
        self.assertFalse(spots & walls, "nothing laid on an inside wall")
        self.assertIn("redesigned", self.home["grown"][-1]["what"])
        # A place made after stands on none of the mansion's cells.
        self.keeper.apply({"content_switch": {"switched": True, "what": "x"},
                           "zones": [{"kind": "pool", "name": "Lido", "why": "w"}]}, [], "2026-11-20")
        world = self.keeper.load()
        m, r = pp.zone_rect(world["zones"][0]), pp.zone_rect(world["zones"][-1])
        self.assertFalse(r[0] < m[2] + pp.GAP and r[2] > m[0] - pp.GAP and r[1] < m[3] + pp.GAP and r[3] > m[1] - pp.GAP)

    def test_outdoor_places_and_a_pools_water_kept_free(self) -> None:
        from personalized_agent.agent import playplaces as pp
        said = {"content_switch": {"switched": True, "what": "swimming"},
                "zones": [{"kind": "pool", "name": "Lido", "why": "w"}],
                "items": [{"zone": "Lido", "kind": "lounger", "name": f"l{n}", "why": "w", "from": "f"} for n in range(6)]}
        self.keeper.apply(said, [], "2026-11-17")
        world = self.keeper.load()
        pool = world["zones"][-1]
        water = pp.pool_water(pool)
        self.assertTrue(water)
        self.assertFalse({(one["x"], one["y"]) for one in world["items"] if one["zone"] == pool["id"]} & water)


class DrawnFromWhatTheAgentKnowsTests(unittest.TestCase):
    """2026-11-17: "建图的输入改成以 agent 的任务、状态、项目为主，并允许它画之前先搜一轮" - "A": its own records only."""

    def test_its_projects_tasks_and_states_first_and_a_search_of_its_records_before_it_draws(self) -> None:
        from support import TemporaryAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        db = agent.app.db
        db.execute("INSERT INTO state_projects (id, scope, name, why, created_at, updated_at) VALUES "
                   "('p1', 'live', 'Hive census', 'Counting the bees of the orchard', '2026-10-01', '2026-10-05')")
        db.execute("INSERT INTO states (id, title, about, created_at, updated_at) VALUES "
                   "('s1', 'Weighing the honey frames', 'user', '2026-10-03T10:00:00', '2026-10-03T10:00:00')")
        db.execute("INSERT INTO conversations (id, created_at) VALUES ('c1', '2026-10-01T00:00:00+00:00')")
        db.execute("INSERT INTO messages (id, conversation_id, created_at, role, content) VALUES "
                   "('m1', 'c1', '2026-10-04T09:00:00', 'user', 'The census found four hundred drones')")
        lived = pw.lived_between(db, "2026-10-01", "2026-10-15")
        self.assertLess(lived.index("THEIR PROJECTS"), lived.index("THEY SAID"), "what the agent knows first")
        self.assertIn("Hive census", lived)
        self.assertIn("Weighing the honey frames", lived)
        with tempfile.TemporaryDirectory() as notes:
            (Path(notes) / "orchard.md").write_text("The orchard has twelve hives by the pear trees.", encoding="utf-8")
            found = pw.look_up(db, Path(notes), ["census drones", "hives pear"], "2026-10-15")
        self.assertIn("four hundred drones", found)
        self.assertIn("[memory orchard]", found)
        # Before it draws: asked what it wants to know, given what was found - two calls, both the world's own.
        with tempfile.TemporaryDirectory() as folder:
            class Two(Models):
                def invoke(self, request, role=""):
                    self.asked.append(request)
                    said = {"look_up": ["census"]} if request.task == pw.TASK_LOOK else {"items": []}
                    return SimpleNamespace(text=json.dumps(said))
            asked = []
            keeper = pw.PlayWorld(Path(folder), Two({}), SimpleNamespace(get=lambda key, default=None: default),
                                  history=lambda since, until: "lived",
                                  look=lambda questions, until: asked.extend(questions) or "Q: census  [chat] drones")
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            self.assertEqual(asked, ["census"])
            tasks = [one.task for one in keeper.models.asked]
            # 2026-11-18: then its words put in both languages ("让他做双语翻译").
            # The home's room designed once (2026-11-19); written in the language shown, nothing translated.
            # 2026-11-20: then the world's looks as a whole ("在交通规划之后加一个stage, 是整体美学规划").
            self.assertEqual(tasks, [pw.TASK_LOOK, pw.TASK_THEMES, pw.TASK, pw.muse.TASK_ROOM, pw.muse.TASK_AESTHETICS])
            self.assertIn("looked_up", [a for a in keeper.models.asked if a.task == pw.TASK][-1].messages[0]["content"])
        from personalized_agent.common import modules
        self.assertEqual(modules.module_of(pw.TASK_LOOK), "play_world", "its tokens the world's")


class TheirTastesTests(unittest.TestCase):
    """2026-11-17: "你的设计也可以retrieve 用户的preference来决定你设计的风格"."""

    def test_the_lines_that_say_a_taste_given_to_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as notes:
            (Path(notes) / "MEMORY.md").write_text("- [Home](home.md) - their flat", encoding="utf-8")
            (Path(notes) / "home.md").write_text(chr(10).join(["They live near a river.",
                                                           "They love deep greens and old wood.", "不要太亮的颜色。"]),
                                                 encoding="utf-8")
            said = pw.tastes_of(Path(notes))
        self.assertIn("deep greens", said)
        self.assertIn("不要太亮的颜色", said)
        self.assertNotIn("near a river", said, "only what says a taste")
        # Their own words, from their chats - the sentence that says it, not the whole message.
        from support import TemporaryAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        db = agent.app.db
        db.execute("INSERT INTO conversations (id, created_at) VALUES ('c1', '2026-10-01T00:00:00+00:00')")
        db.execute("INSERT INTO messages (id, conversation_id, created_at, role, content) VALUES "
                   "('m1', 'c1', '2026-10-04T09:00:00', 'user', '今天去了果园。我喜欢暖一点的木头颜色。明天再说。'), "
                   "('m2', 'c1', '2026-10-05T09:00:00', 'assistant', 'I like that too')")
        said = pw.tastes_of(None, db)
        self.assertIn("我喜欢暖一点的木头颜色。", said)
        self.assertNotIn("今天去了果园", said, "only the sentence that says a taste")
        self.assertNotIn("I like that too", said, "their words, not the agent's")
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Models({}), SimpleNamespace(get=lambda key, default=None: default),
                                  tastes=lambda: "THEIR TASTES: deep greens")
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            grown = [a for a in keeper.models.asked if a.task == pw.TASK][-1]
            self.assertIn("deep greens", grown.messages[0]["content"])
            self.assertIn("THEIR TASTES", grown.system)


class NotOnAGridTests(unittest.TestCase):
    """2026-11-17: "你不要这种网状的" - and, of the world already there, "重新摆"."""

    def test_an_old_grid_world_placed_again_once_its_things_moved_with_it(self) -> None:
        from personalized_agent.agent import playplaces as pp
        with tempfile.TemporaryDirectory() as folder:
            old = {"version": 1, "born": "2026-11-01", "caught_up": "2026-11-10", "growths": [],
                   "zones": [{"id": "z1", "kind": "home", "name": "Home", "made": "2026-11-01", "cell": [0, 0],
                              "x": 1, "y": 0, "w": 11, "h": 6, "door": 7, "grown": []},
                             {"id": "z2", "kind": "studio", "name": "Studio", "made": "2026-11-02", "cell": [1, 0],
                              "x": 19, "y": 0, "w": 11, "h": 6, "door": 25, "grown": []}],
                   "items": [{"id": "i1", "zone": "z2", "kind": "desk", "x": 20, "y": 3, "made": "2026-11-02"}]}
            (Path(folder) / "world.json").write_text(json.dumps(old), encoding="utf-8")
            keeper = pw.PlayWorld(Path(folder), Models({}), SimpleNamespace(get=lambda key, default=None: default))
            world = keeper.load()
            studio = world["zones"][1]
            desk = world["items"][0]
            self.assertEqual((desk["x"] - studio["x"], desk["y"] - studio["y"]), (1, 3), "its things moved with it")
            self.assertEqual(studio["door"] - studio["x"], 6)
            self.assertNotIn("cell", studio)
            self.assertEqual(world["placed"], 2)
            again = json.loads((Path(folder) / "world.json").read_text(encoding="utf-8"))
            self.assertEqual(again["zones"][1]["x"], studio["x"], "kept so - placed once")

    def test_places_gather_loosely_not_in_rows(self) -> None:
        from personalized_agent.agent import playplaces as pp
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Models({}), SimpleNamespace(get=lambda key, default=None: default))
            world = keeper.world()
            for n in range(6):
                keeper._add_zone(world, "study", f"s{n}", "w", "2026-11-17")
            rects = [pp.zone_rect(one) for one in world["zones"]]
            for a in range(len(rects)):
                for b in range(a + 1, len(rects)):
                    p, q = rects[a], rects[b]
                    self.assertFalse(p[0] < q[2] + pp.GAP and p[2] > q[0] - pp.GAP and p[1] < q[3] + pp.GAP
                                     and p[3] > q[1] - pp.GAP, "a path's room between every two")
            self.assertGreater(len({r[1] for r in rects}), 2, "not all on a few rows")


class EveryImportantTaskAPlaceTests(unittest.TestCase):
    """2026-11-17: "每个重要的task都有单独的房间/房子/景色之类，里面最好有task相关的item,可以从task的素材里找"."""

    def test_an_important_task_named_with_its_materials_and_given_a_place_without_a_switch(self) -> None:
        from PIL import Image
        from support import TemporaryAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        db = agent.app.db
        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "hive.jpg"
            Image.new("RGB", (400, 300), (200, 160, 40)).save(picture)
            db.execute("INSERT INTO state_tasks (id, number, members, described, created_at, updated_at) VALUES "
                       "('t1', 1, '[\"s1\"]', ?, '2026-10-01', '2026-10-05'), "
                       "('t2', 2, '[\"s2\"]', ?, '2026-10-01', '2026-10-05'), "
                       "('t3', 3, '[\"s3\"]', ?, '2026-10-01', '2026-10-05'), "
                       "('t4', 4, '[\"s4\"]', ?, '2026-10-01', '2026-10-05')",
                       (json.dumps({"title": "Hive census", "importance": 5, "summary": "Counting bees", "minutes": 300}),
                        json.dumps({"title": "Tidy downloads", "importance": 1, "minutes": 5}),
                        json.dumps({"title": "Chess evenings", "kind": "leisure", "minutes": 75}),
                        json.dumps({"title": "Quick look", "importance": 9, "minutes": 40})))
            db.execute("INSERT INTO state_materials (state_id, event_id, kind, title, copies, created_at) VALUES "
                       "('s1', 'e1', 'file', 'census.xlsx', '{}', '2026-10-02'), "
                       "('s1', 'e2', 'picture', 'the north hive', ?, '2026-10-03'), "
                       "('s1', 'e3', 'screen', 'a screen', '{}', '2026-10-03')", (json.dumps({"picture": str(picture)}),))
            found = pw.important_tasks(db, "2026-10-01", "2026-10-15")
            # 2026-11-17: "你理论上不要用固定的重要任务个数，只要比如总activity超过1小时就算"; "包括lesisure task 也要作为一个空间".
            big = [one["title"] for one in found if not one.get("small")]
            self.assertEqual(big, ["Hive census", "Chess evenings"], "an hour or more, leisure too")
            self.assertTrue(next(one for one in found if one["title"] == "Chess evenings").get("leisure"))
            # 2026-11-17: "对于小任务 ... 可以考虑对小任务进行grouping进行完整景观建造" - under an hour, at least 20 minutes:
            # small, to be grouped; five minutes, nothing.
            self.assertEqual([one["title"] for one in found if one.get("small")], ["Quick look"])
            found = [one for one in found if not one.get("small")]
            self.assertIn("file: census.xlsx", found[0]["materials"])
            self.assertEqual(found[0]["pictures"][0]["path"], str(picture))
            self.assertFalse(any("screen" in one for one in found[0]["materials"]), "never the screen's")
            said = {"content_switch": {"switched": False},
                    "zones": [{"kind": "study", "name": "Hive census", "why": "w", "task": "t1"},
                              {"kind": "cafe", "name": "Not new", "why": "w"}],
                    "items": [{"zone": "Hive census", "kind": "frame", "name": "North hive", "why": "w",
                               "from": "picture: the north hive", "picture": "task-t1-1"}]}
            keeper = pw.PlayWorld(Path(folder) / "w", Models(said), SimpleNamespace(get=lambda key, default=None: default),
                                  tasks=lambda since, until: found)
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            names = [one["name"] for one in world["zones"]]
            self.assertIn("Hive census", names, "its own place, though nothing else switched")
            self.assertNotIn("Not new", names, "no other new place without a switch")
            place = next(one for one in world["zones"] if one["name"] == "Hive census")
            self.assertEqual(place["task"], "t1")
            frame = next(one for one in world["items"] if one["kind"] == "frame")
            self.assertTrue(frame["shot"], "the task's picture in its frame's card")
            self.assertIn("important_tasks", [a for a in keeper.models.asked if a.task == pw.TASK][-1].messages[0]["content"])
        # As large as the task: the minutes it took and how much it holds.
        self.assertEqual([pw.task_scale(w) for w in (60, 300, 900, 4000)], ["first", "full", "large", "estate"])
        self.assertLess(pw.task_things(60, 1), pw.task_things(900, 10))
        self.assertLessEqual(pw.task_things(99999, 99), pw.MOST_NEW_ITEMS)


class DrawnOnOrFromTheStartTests(unittest.TestCase):
    """2026-11-17: "你这个重画一次不要写到代码里 ... 之前画的被删掉了，当前没有内容的时候就重画，不然就接着画"."""

    def test_a_world_there_drawn_on_none_there_drawn_from_the_start(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            old = {"version": 1, "born": "2026-11-01", "caught_up": "2026-11-10", "growths": [], "placed": 2,
                   "zones": [{"id": "z1", "kind": "home", "name": "Home", "made": "2026-11-01", "x": 4, "y": 1, "w": 11,
                              "h": 6, "grown": []}], "items": []}
            (Path(folder) / "world.json").write_text(json.dumps(old), encoding="utf-8")
            keeper = pw.PlayWorld(Path(folder), Models({}), SimpleNamespace(get=lambda key, default=None: default))
            self.assertEqual(keeper.world()["caught_up"], "2026-11-10", "drawn on, never again by itself")
            (Path(folder) / "world.json").rename(Path(folder) / "aside.json")
            fresh = keeper.world()
            self.assertEqual(len(fresh["zones"]), 1)
            self.assertEqual(fresh["caught_up"], fresh["drawn_from"], "from the start")
        from support import ROOT
        page = (ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        self.assertNotIn("data-play-redraw", page, "no button for it")
        self.assertFalse(hasattr(pw.PlayWorld, "DRAWN_BY"), "no version of the rules")

class AsRichAsTheirLifeTests(unittest.TestCase):
    """2026-11-17: "你现在地图的复杂度和世界大小远低于我的预期"."""

    def test_a_tasks_place_filled_from_its_materials_when_the_model_left_it_short(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            task = {"id": "t1", "title": "Hive census", "summary": "", "weight": 900, "scale": "first", "things": 6,
                    "materials": ["file: census.xlsx", "page: Bee atlas", "message: counts from Sam", "file: map.pdf"],
                    "pictures": []}
            said = {"zones": [{"kind": "study", "name": "Hive census", "why": "w", "task": "t1"}],
                    "items": [{"zone": "Hive census", "kind": "desk", "name": "desk", "why": "w", "from": "file: census.xlsx"}]}
            keeper = pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default),
                                  tasks=lambda since, until: [task])
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            place = next(one for one in world["zones"] if one.get("task") == "t1")
            mine = [one for one in world["items"] if one["zone"] == place["id"]]
            self.assertGreaterEqual(len(mine), 4, "the model's one and the rest from its materials")
            froms = {one["from"] for one in mine}
            self.assertIn("page: Bee atlas", froms)
            self.assertEqual(sum(1 for one in mine if one["from"] == "file: census.xlsx"), 1, "a material used once")
        self.assertGreaterEqual(pw.MOST_NEW_ITEMS, 40)

    def test_what_was_worked_out_before_the_call_is_kept_and_the_place_filled(self) -> None:
        # 2026-11-18: "另外你现在房间里东西密度还是太低了" - no world had kept "tasks" (nor "projects"): `apply` read the
        # world again and what the growth had worked out before its call was lost, so no place was filled.
        with tempfile.TemporaryDirectory() as folder:
            measures = {"hours": 162, "switches": 118, "days": 38, "places": 386, "deliverables": 113, "pieces": 138}
            complexity = pw.complexity_of(measures)
            task = {"id": "t1", "title": "Agent release", "summary": "", "weight": 9000, "scale": "estate", "things": 6,
                    "materials": [], "pictures": [], "day_minutes": {"2026-10-01": 300.0}, "measures": measures,
                    "complexity": complexity, "fill": pw.fill_of(complexity)}
            said = {"zones": [{"kind": "lab", "name": "Agent works", "why": "w", "task": "t1"}],
                    "items": [{"zone": "Agent works", "kind": "computer", "name": "The first build", "why": "w"},
                              {"zone": "Agent works", "kind": "robot", "name": "Its helper", "why": "w"}]}
            keeper = pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default),
                                  tasks=lambda since, until: [task])
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            self.assertIn("t1", world.get("tasks") or {}, "the task's record kept")
            self.assertAlmostEqual(world["tasks"]["t1"]["fill"], pw.fill_of(complexity))
            place = next(one for one in world["zones"] if one.get("task") == "t1")
            self.assertLess(keeper._free_share(world, place), 0.5, "filled to its complexity")
            source = Path(pw.__file__).read_text(encoding="utf-8")
            self.assertLess(source.index("            self.save(world)\n        try:\n            response = self.models.invoke"),
                            source.index("grown = self.apply(said, offered, at, (since, until))"), "kept before the call")


class CaughtUpWithoutWaitingTests(unittest.TestCase):
    """2026-11-18: "还没追上时，画完一段马上接着画下一段，不再停 5 分钟 ... 是的"; "重启后也立刻检查，不用先等 5 分钟。 也是的"."""

    def test_looked_at_soon_after_start_and_each_part_drawn_at_once_while_behind(self) -> None:
        keeper = pw.PlayWorld.__new__(pw.PlayWorld)
        waits, grown = [], []
        results = iter([{"zones": 2}, {"zones": 1}, {"error": "the model did not answer"}, {"zones": 1}])
        behind = iter([True, True, True, True, False, False])

        class Stop:
            def wait(self, seconds):
                waits.append(seconds)
                return len(waits) > 5

        keeper._stop = Stop()
        keeper.behind = lambda *a, **k: next(behind, False)
        keeper.due = lambda: True
        keeper.grow = lambda: grown.append(1) or next(results)
        keeper._watch()
        self.assertEqual(waits[0], pw.START_SECONDS, "looked at soon after the agent starts")
        self.assertEqual(waits[1:3], [pw.CHAIN_SECONDS, pw.CHAIN_SECONDS], "the next part at once while behind")
        self.assertEqual(waits[3], pw.BEHIND_SECONDS, "a part that failed waits as before")
        self.assertLessEqual(pw.START_SECONDS, 30)


class AProjectsStoryTests(unittest.TestCase):
    """2026-11-18: "你做的建筑是要对user有纪念意义的，比如一个project, 你要让user 看到他的成果是如何一步步变好的，比如personal agent
    一开始的demo是怎么样，后面又是怎么样这种"."""

    def test_its_steps_with_its_own_pictures_of_then_set_up_in_order(self) -> None:
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            shots = []
            for n, colour in enumerate([(200, 40, 40), (40, 160, 220)]):
                shot = folder / f"shot{n}.png"
                picture = Image.new("RGB", (120, 80))
                for x in range(120):
                    for y in range(80):
                        picture.putpixel((x, y), ((x * (3 + n)) % 256, (y * 5) % 256, colour[2]))
                picture.save(shot)
                shots.append(shot)

            class Rows:
                def query(self, sql, params=()):
                    if "state_run_tasks" in sql:
                        return [{"starts_at": "2026-10-03T10:00", "minutes": 300, "title": "The first demo",
                                 "summary": "A chat box only.", "states": '["s1"]'},
                                {"starts_at": "2026-10-25T10:00", "minutes": 500, "title": "The sidebar",
                                 "summary": "Its own panel.", "states": '["s2"]'}]
                    if "state_materials" in sql:
                        shot = shots[0] if "s1" in params else shots[1]
                        return [{"created_at": "2026-10-03", "title": "the app", "copies": json.dumps({"picture": str(shot)})}]
                    return []
            story = pw.project_story(Rows(), "p1", "2026-11-01")
            self.assertEqual([one["step"] for one in story], ["2026-10-0", "2026-10-2"], "steps of about ten days")
            self.assertEqual(story[0]["did"], ["The first demo"])
            self.assertTrue(story[0]["pictures"] and story[1]["pictures"], "its own pictures of then")
            self.assertEqual(pw.story_step("2026-10-15"), "2026-10-1")
            keeper = pw.PlayWorld.__new__(pw.PlayWorld)
            keeper.folder = folder / "world"
            zone = {"id": "z1", "kind": "lab", "x": 0, "y": 0, "w": 16, "h": 12, "door": 8, "task": "p1"}
            world = {"zones": [zone], "items": [], "regions": []}
            pictures = {p["id"]: p for step in story for p in step["pictures"]}
            said = [{"task": "p1", "step": "2026-10-2", "name": "The sidebar arrives", "why": "It grew a panel.",
                     "picture": story[1]["pictures"][0]["id"]},
                    {"task": "p1", "step": "2026-10-0", "name": "The first demo", "why": "A chat box, nothing more.",
                     "picture": story[0]["pictures"][0]["id"]}]
            self.assertEqual(keeper._lay_story(world, said, pictures, "2026-11-01"), 2)
            first, second = world["items"]
            self.assertEqual((first["story_step"], second["story_step"]), ("2026-10-0", "2026-10-2"), "in the story's order")
            self.assertLess((first["y"], first["x"]), (second["y"], second["x"]), "across, then down")
            self.assertEqual(first["kind"], "statue_of")
            # 2026-11-18: "室内的东西的size就正常": in a room, its own size; on open ground, its picture large.
            self.assertNotIn("scale", first, "a lab is a room")
            self.assertTrue((keeper.folder / "pictures" / first["shot"]).is_file(), "its picture in its card")
            self.assertEqual(keeper._lay_story(world, said, pictures, "2026-11-02"), 0, "a step set up once")
        self.assertIn('"story": [{{"task": "<task id>", "step": "<its step>"', pw.SYSTEM)
        source = Path(pw.__file__).read_text(encoding="utf-8")
        self.assertIn('items_added += self._lay_story(world, said.get("story"), pictures, at)', source)


class KnownAtAGlanceTests(unittest.TestCase):
    """2026-11-17: "<a project's> 房间门牌还是没有<its name> ... 允许model用项目的logo做（或者比如在地上铺成那个项目的字 ... 也允许
    agent自己作画铺在地上)"."""

    def test_a_place_made_its_projects_its_floor_its_name_put_back(self) -> None:
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            logo = Path(folder) / "logo.png"
            Image.new("RGB", (64, 64), (180, 30, 30)).save(logo)
            task = {"id": "t9", "title": "StarCatalog", "summary": "", "materials": [], "things": 3, "scale": "first",
                    "pictures": [{"id": "task-t9-1", "path": str(logo), "what": "logo"}]}
            keeper = pw.PlayWorld(Path(folder) / "w", Models({}), SimpleNamespace(get=lambda key, default=None: default),
                                  tasks=lambda since, until: [task])
            world = keeper.world()
            old = keeper._add_zone(world, "study", "Paper Room", "w", "2026-11-01")
            keeper.save(world)
            keeper.models = Models({"decorate": [{"zone": old["id"], "task": "t9", "name": "StarCatalog Reading Room",
                                                  "floor_text": "StarCatalog", "floor_color": "#b31b1b",
                                                  "floor_picture": "task-t9-1",
                                                  "mural": {"palette": ["#b31b1b", "nothex"], "rows": ["00..", "x0z0"]}},
                                                 {"zone": "z1", "name": "Elsewhere"}]})
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            place = next(one for one in world["zones"] if one["id"] == old["id"])
            self.assertEqual((place["task"], place["task_title"], place["name"]), ("t9", "StarCatalog", "StarCatalog Reading Room"))
            self.assertEqual((place["floor_text"], place["floor_color"]), ("StarCatalog", "#b31b1b"))
            self.assertTrue(place["floor_picture"], "its logo laid on its floor")
            self.assertEqual(place["mural"], {"palette": ["#b31b1b"], "rows": ["00..", ".0.0"]}, "checked")
            self.assertNotEqual(world["zones"][0]["name"], "Elsewhere", "a name changed only to put back a task's own")


class ATasksGroundTests(unittest.TestCase):
    """2026-11-17: "task越复杂，地盘越大，但是可以是内景也可以是外景或者两个都有（比如带院子的房子），让模型自己判断"."""

    def test_a_house_with_its_garden_beside_it_both_the_tasks(self) -> None:
        from personalized_agent.agent import playplaces as pp
        with tempfile.TemporaryDirectory() as folder:
            task = {"id": "t5", "title": "Hive census", "summary": "", "materials": [], "pictures": [], "things": 3,
                    "scale": "large", "weight": 900}
            said = {"zones": [{"kind": "study", "name": "Hive census", "why": "w", "task": "t5", "grounds": "garden",
                               "grounds_name": "Hive census Meadow"}]}
            keeper = pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default),
                                  tasks=lambda since, until: [task])
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            house = next(one for one in world["zones"] if one["name"] == "Hive census")
            yard = next(one for one in world["zones"] if one["name"] == "Hive census Meadow")
            self.assertEqual((yard["kind"], yard["task"], yard["grounds_of"]), ("garden", "t5", house["id"]))
            self.assertEqual(yard["span"], [2, 1], "a complex task's ground the larger")
            h, y = pp.zone_rect(house), pp.zone_rect(yard)
            gap = max(y[0] - h[2], h[0] - y[2], y[1] - h[3], h[1] - y[3])
            self.assertLess(gap, 16, "beside it")


class CustomizedThingsTests(unittest.TestCase):
    """2026-11-17: "让模型用project相关的图片生成内饰，比如生成一个雕塑，图片之类，有customized东西"."""

    def test_a_statue_of_its_logo_and_a_thing_the_agent_drew_checked(self) -> None:
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            logo = Path(folder) / "logo.png"
            Image.new("RGB", (64, 64), (30, 90, 200)).save(logo)
            robot = {"palette": ["#888888", "#ff0000"], "rows": ["..0000..", ".011110.", ".000000.", "..0..0.."]}
            said = {"items": [{"zone": "z1", "kind": "statue_of", "name": "Logo statue", "why": "w", "from": "f",
                               "picture": "pic-1"},
                              {"zone": "z1", "kind": "pixel_art", "name": "Robot", "why": "w", "from": "f", "pixels": robot},
                              {"zone": "z1", "kind": "pixel_art", "name": "Nothing", "why": "w", "from": "f",
                               "pixels": {"palette": ["#000000"], "rows": ["0"]}},
                              {"zone": "z1", "kind": "banner", "name": "No picture", "why": "w", "from": "f"}]}
            keeper = pw.PlayWorld(Path(folder) / "w", Models(said), SimpleNamespace(get=lambda key, default=None: default),
                                  pictures=lambda: [{"id": "pic-1", "path": str(logo), "what": "logo", "when": ""}])
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            names = {one["name"]: one for one in keeper.load()["items"]}
            self.assertTrue(names["Logo statue"]["shot"], "a statue on the map, its logo in its card")
            self.assertEqual(names["Robot"]["pixels"]["rows"][1], ".011110.")
            self.assertNotIn("Nothing", names, "too little drawn: not made")
            self.assertIn("No picture", names, "a banner without one of theirs: a cartoon banner (2026-11-19)")


class TheGroundBetweenTests(unittest.TestCase):
    """2026-11-17: "如果一个区域紧挨着A, B, C 3个project, 模型会根据这3个关键词的combination进行构思（比如进行总结，抽象出新的关键词），
    进行设计"."""

    def test_neighbouring_projects_offered_and_a_place_of_their_shared_idea_made_between_them(self) -> None:
        from personalized_agent.agent import playplaces as pp
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Models({}), SimpleNamespace(get=lambda key, default=None: default))
            world = keeper.world()
            for n, title in enumerate(("Robot Arm", "Paper Reader", "Bee Census")):
                zone = keeper._add_zone(world, "study", title, "w", "2026-11-17")
                zone["task"], zone["task_title"] = f"t{n}", title
            keeper.save(world)
            groups = keeper._neighbourhoods(world)
            self.assertTrue(groups and 2 <= len(groups[0]["zones"]) <= 3)
            ids = groups[0]["zones"]
            keeper.models = Models({"content_switch": {"switched": False},
                                    "zones": [{"kind": "plaza", "name": "Thinking Machines", "why": "robots that read",
                                               "between": ids}]})
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            made = next(one for one in world["zones"] if one["name"] == "Thinking Machines")
            self.assertEqual(sorted(made["between"]), sorted(ids), "made though nothing else switched")
            self.assertEqual(len(made["between_names"]), len(ids))
            self.assertIn("neighbourhoods", [a for a in keeper.models.asked if a.task == pw.TASK][-1].messages[0]["content"])
            self.assertNotIn(tuple(sorted(ids)), {tuple(g["zones"]) for g in keeper._neighbourhoods(world)}, "joined now")


class TheRoadOfTheirLifeTests(unittest.TestCase):
    """2026-11-17: "你的路上要有路标，箭头，表示你做这些project的顺序（整体还是要能够按照顺序从一端走到另一端，走过整个user的life)";
    "比如一个时间开始做几个independent projects, 就从一个地方分出几个岔路这样"; "这个在选layout也要考虑"."""

    def _world(self, keeper, spans):
        world = keeper.world()
        for name, days in spans:
            zone = keeper._add_zone(world, "study", name, "w", "2026-11-17", towards=keeper._after(world, "study", days))
            zone["days"] = days
        return world

    def test_begun_together_they_fork_and_after_them_the_branches_join(self) -> None:
        from personalized_agent.agent import playplaces as pp
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Models({}), SimpleNamespace(get=lambda key, default=None: default))
            world = self._world(keeper, [("A", ["2026-09-01", "2026-09-10"]), ("B", ["2026-09-12", "2026-09-25"]),
                                         ("C", ["2026-09-13", "2026-09-28"]), ("D", ["2026-10-02", "2026-10-20"])])
            ids = {one["name"]: one["id"] for one in world["zones"]}
            road = pp.life_road(world)
            self.assertEqual(road[ids["A"]], [])
            self.assertEqual(road[ids["B"]], [ids["A"]])
            self.assertEqual(road[ids["C"]], [ids["A"]], "begun together: both from A, a fork")
            self.assertEqual(sorted(road[ids["D"]]), sorted([ids["B"], ids["C"]]), "after both: joined again")
            self.assertNotIn(ids.get("Home", "z1"), road, "a place without its days is not on the road")

    def test_a_place_is_laid_on_from_what_it_follows(self) -> None:
        import math
        from personalized_agent.agent import playplaces as pp
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Models({}), SimpleNamespace(get=lambda key, default=None: default))
            world = self._world(keeper, [("A", ["2026-09-01", "2026-09-10"]), ("B", ["2026-09-12", "2026-09-25"])])
            a, b = (next(one for one in world["zones"] if one["name"] == n) for n in "AB")
            towards = keeper._after(world, "study", ["2026-10-01", "2026-10-05"])
            ca, cb = pp.centre(pp.zone_rect(a)), pp.centre(pp.zone_rect(b))
            self.assertLess(math.hypot(towards[0] - cb[0], towards[1] - cb[1]), math.hypot(towards[0] - ca[0], towards[1] - ca[1]))
            self.assertIsNone(keeper._after(world, "lake", ["2026-10-01", "2026-10-05"]), "a natural place: not on the road")

    def test_the_page_lays_the_same_road_with_its_signposts(self) -> None:
        page = (WEB / "play-regions.js").read_text(encoding="utf-8")
        for name in ("function playLifeOf", "function playLifeRoad", "PLAY_LIFE_JOIN_DAYS = 21", "start: true"):
            self.assertIn(name, page)
        self.assertIn("if (sign.post) { playPostDraw(", (WEB / "play.js").read_text(encoding="utf-8"))


class OnTheWayItGoesTests(unittest.TestCase):
    """2026-11-17: "比如 place A -> B, -> C 基本在同一个线上，那么C的style是类似于从B向从A到B的方向过度（让模型根据这个思路想一个主题）";
    "所以现在有除了模型直接想之外，还有directional和combinational的想主题的方式"."""

    def test_a_then_b_offered_and_c_made_beyond_b_on_their_line(self) -> None:
        import math
        from personalized_agent.agent import playplaces as pp
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Models({}), SimpleNamespace(get=lambda key, default=None: default))
            world = keeper.world()
            for name, days in (("Cottage", ["2026-09-01", "2026-09-10"]), ("Manor", ["2026-09-12", "2026-09-25"])):
                zone = keeper._add_zone(world, "study", name, "w", "2026-11-17", towards=keeper._after(world, "study", days))
                zone["days"] = days
            keeper.save(world)
            a, b = (next(z for z in world["zones"] if z["name"] == n) for n in ("Cottage", "Manor"))
            [line] = [one for one in keeper._lines(world) if one["zones"] == [a["id"], b["id"]]]
            self.assertEqual((line["from"]["name"], line["to"]["name"]), ("Cottage", "Manor"))
            keeper.models = Models({"content_switch": {"switched": False},
                                    "zones": [{"kind": "study", "name": "Palace", "why": "a cottage, a manor, then this",
                                               "goes_on": [a["id"], b["id"]], "style": "mansion"}]})
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            c = next(z for z in world["zones"] if z["name"] == "Palace")
            self.assertEqual(c["goes_on"], [a["id"], b["id"]], "made though nothing else switched")
            self.assertEqual(c["style"], "mansion")
            (ax, ay), (bx, by), (cx, cy) = (pp.centre(pp.zone_rect(z)) for z in (a, b, c))
            self.assertGreater((bx - ax) * (cx - bx) + (by - ay) * (cy - by), 0, "beyond B, the way A came to it")
            self.assertIn('"lines"', json.dumps(json.loads(next(r for r in keeper.models.asked if r.task == pw.TASK).messages[0]["content"]), ensure_ascii=False))
            self.assertFalse([one for one in keeper._lines(world) if one["zones"] == [a["id"], b["id"]]], "gone on now")


class ApartTogetherMergedTests(unittest.TestCase):
    """2026-11-17: "孤岛，或者孤独山洞，反正只能用传送门到"; "相似的task(内容和时间）尽可能排在一起"; "任务之后可以合并，规划成一个小镇或者
    大house这种"; "那个牌匾（标志）也有几种style"."""

    def _keeper(self, folder, said, tasks=()):
        return pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default),
                            tasks=lambda since, until: list(tasks))

    def test_a_task_unlike_all_stands_apart_joined_to_nothing(self) -> None:
        task = {"id": "t9", "title": "Glass blowing", "summary": "s", "materials": [], "lived": ["2026-11-01", "2026-11-10"]}
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder, {"content_switch": {"switched": False},
                                           "zones": [{"kind": "studio", "name": "Glass Isle", "why": "w", "task": "t9",
                                                      "apart": "cave", "sign": "stone"}]}, [task])
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            zone = next(z for z in world["zones"] if z["name"] == "Glass Isle")
            region = next(r for r in world["regions"] if r["id"] == zone["region"])
            self.assertEqual((region["apart"], region["from"], region["biome"]), ("cave", None, "cavern"))
            others = [tuple(r["cell"]) for r in world["regions"] if r is not region]
            self.assertFalse(any(max(abs(c[0] - region["cell"][0]), abs(c[1] - region["cell"][1])) <= 1 for c in others),
                             "no land beside it")
            self.assertEqual(zone["sign"], "stone")

    def test_merged_into_a_town_they_stand_round_its_square(self) -> None:
        import math
        from personalized_agent.agent import playplaces as pp
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder, {})
            world = keeper.world()
            made = [keeper._add_zone(world, "study", n, "w", "2026-11-17") for n in ("Bees", "Hives", "Honey")]
            keeper._add_item(world, made[0], {"kind": "plant"}, "2026-11-17")
            before = [(i["x"] - made[0]["x"], i["y"] - made[0]["y"]) for i in world["items"] if i["zone"] == made[0]["id"]]
            square = keeper._merge(world, {"into": "town", "name": "Bee Town", "zones": [z["id"] for z in made]}, "2026-11-18")
            self.assertEqual(square["town_names"], ["Bees", "Hives", "Honey"])
            sx, sy = pp.centre(pp.zone_rect(square))
            for one in made:
                cx, cy = pp.centre(pp.zone_rect(one))
                self.assertLess(math.hypot(cx - sx, cy - sy), 30, "round its square")
            after = [(i["x"] - made[0]["x"], i["y"] - made[0]["y"]) for i in world["items"] if i["zone"] == made[0]["id"]]
            self.assertEqual(before, after, "its things moved with it")

    def test_merged_into_a_great_house_each_a_room_nothing_lost(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder, {})
            world = keeper.world()
            made = [keeper._add_zone(world, "study", n, "w", "2026-11-17") for n in ("Reading", "Writing")]
            made[0]["task"], made[1]["task"] = "t1", "t2"
            for one in made:
                keeper._add_item(world, one, {"kind": "plant"}, "2026-11-17")
            things = len(world["items"])
            house = keeper._merge(world, {"into": "house", "name": "The Thesis House", "zones": [z["id"] for z in made]},
                                  "2026-11-18")
            self.assertEqual([r["name"] for r in house["rooms"]], ["Reading", "Writing"])
            self.assertEqual(sorted(house["tasks"]), ["t1", "t2"])
            self.assertFalse({z["id"] for z in made} & {z["id"] for z in world["zones"]}, "the merged gone")
            self.assertEqual(len(world["items"]), things, "nothing lost")
            self.assertTrue(all(i["zone"] == house["id"] for i in world["items"] if i.get("room")))


class SmallTasksOneSceneTests(unittest.TestCase):
    """2026-11-17: "对于小任务，可能分到的地盘特别小，可以考虑对小任务进行grouping进行完整景观建造"; and a task made before its time
    was counted (before 2026-10-19) still weighed - by its states."""

    def test_small_tasks_grouped_into_one_place_each_a_thing_in_it(self) -> None:
        small = [{"id": f"s{n}", "title": t, "summary": "", "materials": [], "small": True, "weight": 30}
                 for n, t in enumerate(("Fix a lamp", "Water plants", "Oil a hinge"))]
        with tempfile.TemporaryDirectory() as folder:
            said = {"content_switch": {"switched": False},
                    "zones": [{"kind": "garden", "name": "Odd Jobs Garden", "why": "small mendings", "tasks": ["s0", "s1", "s2"]}],
                    "items": [{"zone": "Odd Jobs Garden", "kind": "plant", "name": "the watered one", "why": "w", "task": "s1"}]}
            keeper = pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default),
                                  tasks=lambda since, until: small)
            keeper.world()
            keeper.grow("2026-11-17T20:00:00+00:00")
            world = keeper.load()
            zone = next(z for z in world["zones"] if z["name"] == "Odd Jobs Garden")
            self.assertEqual(zone["tasks"], ["s0", "s1", "s2"], "made though nothing switched")
            self.assertEqual(zone["group_titles"], ["Fix a lamp", "Water plants", "Oil a hinge"])
            self.assertEqual(next(i for i in world["items"] if i["zone"] == zone["id"])["task"], "s1")
            asked = json.loads(next(r for r in keeper.models.asked if r.task == pw.TASK).messages[0]["content"])
            self.assertEqual([one["id"] for one in asked["small_tasks"]], ["s0", "s1", "s2"])
            self.assertNotIn("important_tasks", asked, "none of them a place of its own")

    def test_an_old_tasks_time_from_its_states(self) -> None:
        from support import TemporaryAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        db = agent.app.db
        db.execute("INSERT INTO states (id, title, starts_at, ends_at, metadata, created_at, updated_at) VALUES "
                   "('a', 'x', '2026-10-01T10:00:00+00:00', '2026-10-01T11:00:00+00:00', '{\"minutes\": 50}', '2026-10-01', '2026-10-01'), "
                   "('b', 'y', '2026-10-02T10:00:00+00:00', '2026-10-02T10:30:00+00:00', '{\"minutes\": 90}', '2026-10-02', '2026-10-02'), "
                   "('c', 'z', '2026-10-03T10:00:00+00:00', '2026-10-03T10:20:00+00:00', '{}', '2026-10-03', '2026-10-03')")
        self.assertEqual(pw._states_minutes(db, ["a", "b", "c"]), 50 + 30 + 20, "its own minutes, held to its span")


class AProjectsGroundTests(unittest.TestCase):
    """2026-11-18: "区域的size大小是和花时间正相关的 ... 用多个控件填满"; "地区大小代表了project花的时长 ... 地区内的复杂程度代表了
    project的复杂度"; "后面新加的区域要放在之前的watermelon旁边 ... 把原来在旁边的没那么相关的其它区域移走"; "先conceive, 再
    implementation"; "构思一个call"; "需要有一些半开放的设计"; "世界如果继续变大 可以有多个区域"."""

    def _keeper(self, folder, said, tasks=()):
        return pw.PlayWorld(Path(folder), Models(said), SimpleNamespace(get=lambda key, default=None: default),
                            tasks=lambda since, until: list(tasks))

    def test_its_plot_grows_with_its_hours_and_a_long_one_is_a_district(self) -> None:
        sizes = [pw.plot_size(hours * 60) for hours in (1, 10, 50, 160)]
        areas = [w * h for w, h in sizes]
        self.assertEqual(areas, sorted(areas), "more time, more ground")
        self.assertGreater(areas[-1], 4 * areas[1])

    def test_one_project_gathered_into_its_district_their_things_with_them(self) -> None:
        from personalized_agent.agent import playplaces as pp
        task = {"id": "t1", "title": "Melon App", "summary": "", "materials": [], "weight": 160 * 60, "things": 3}
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder, {}, [task])
            world = keeper.world()
            a = keeper._add_zone(world, "studio", "Melon Studio", "w", "2026-11-17")
            a.update(task="t1", task_title="Melon App", floor_text="Melon")
            b = keeper._add_zone(world, "study", "Melon Lab", "w", "2026-11-17")
            b["floor_text"] = "melon"                       # the same look: the same project
            keeper._add_item(world, b, {"kind": "plant", "why": "w"}, "2026-11-17")
            other = keeper._add_zone(world, "cafe", "Corner Cafe", "w", "2026-11-17")
            keeper._important_info = {"t1": task}
            keeper._plots(world, "2026-11-18")
            plot = next(z for z in world["zones"] if z.get("plot"))
            district = next(r for r in world["regions"] if r.get("district_of") == "t1")
            self.assertEqual(plot["region"], district["id"], "too large for a region: its own")
            inside = (plot["x"], plot["y"] + 1, plot["x"] + plot["w"], plot["y"] + 1 + plot["h"])
            for z in (a, b):
                r = pp.zone_rect(z)
                self.assertEqual(z["region"], district["id"])
                self.assertTrue(inside[0] <= r[0] and inside[1] <= r[1] and r[2] <= inside[2] and r[3] <= inside[3], z["name"])
            thing = next(i for i in world["items"] if i["zone"] == b["id"])
            self.assertTrue(inside[0] <= thing["x"] < inside[2] and inside[1] <= thing["y"] < inside[3], "its things with it")
            self.assertNotEqual(other.get("region"), district["id"], "not its: left where it was")
            # 2026-11-18: "如果对原task影响比较大，可以调整原task内部的layout": grown much, its inside laid again.
            self.assertEqual(plot["laid_area"], plot["w"] * plot["h"])
            moved = []
            real_move = keeper._move
            keeper._move = lambda world, zone, *a, **k: moved.append(zone["id"]) or real_move(world, zone, *a, **k)
            keeper._plots(world, "2026-11-18")
            self.assertEqual(moved, [], "the same size: nothing laid again")
            plot["laid_area"] = plot["w"] * plot["h"] / 2               # as if it had been half the size when laid
            plot["w"] -= 1                                               # (so it is grown again, not kept as it is)
            keeper._plots(world, "2026-11-18")
            self.assertIn(a["id"], moved, "its places gathered afresh")
            keeper._move = real_move
            # 2026-11-18: "模型不要随即摆" - nothing scattered by the code; its ground is the model's to arrange.
            self.assertFalse([i for i in world["items"] if i["zone"] == plot["id"]])
            self.assertGreater(keeper._open_ground(world, "t1"), 1000)
            laid = keeper._arrange(world, {"task": "t1", "kind": "crops", "count": 24, "pattern": "rows", "where": "south",
                                           "name": "Melon rows", "why": "Your versions ripen here, row on row."}, "2026-11-18")
            rows = [i for i in world["items"] if i["zone"] == plot["id"]]
            self.assertEqual(laid, 24)
            self.assertEqual(len({i["group"] for i in rows}), 1, "one design")
            self.assertEqual({i["why"] for i in rows}, {"Your versions ripen here, row on row."}, "one reason for all")
            self.assertEqual(len({(i["x"], i["y"]) for i in rows}), 24, "none on another")
            self.assertTrue(all(inside[1] + (inside[3] - inside[1]) // 2 <= i["y"] < inside[3] for i in rows), "in its south")
            ring = keeper._arrange(world, {"zone": b["id"], "kind": "plant", "count": 6, "pattern": "ring", "where": "middle",
                                           "why": "w"}, "2026-11-18")
            self.assertGreater(ring, 0, "a room's floor arranged too")

    def test_a_plot_beside_its_place_moves_what_is_not_its_aside(self) -> None:
        from personalized_agent.agent import playplaces as pp
        task = {"id": "t2", "title": "Hive Census", "summary": "", "materials": [], "weight": 8 * 60, "things": 3}
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder, {}, [task])
            world = keeper.world()
            main = keeper._add_zone(world, "study", "Hive Census", "w", "2026-11-17")
            main.update(task="t2", task_title="Hive Census")
            neighbour = keeper._add_zone(world, "cafe", "Corner Cafe", "w", "2026-11-17", near=main["id"])
            keeper._important_info = {"t2": task}
            keeper._plots(world, "2026-11-18")
            plot = next(z for z in world["zones"] if z.get("plot"))
            pr, nr = pp.zone_rect(plot), pp.zone_rect(neighbour)
            self.assertFalse(nr[0] < pr[2] and nr[2] > pr[0] and nr[1] < pr[3] and nr[3] > pr[1], "moved off its ground")

    def test_conceived_first_in_a_call_of_its_own_then_built_kept_on_its_place(self) -> None:
        task = {"id": "t3", "title": "WaterMelon", "summary": "an app", "materials": [], "weight": 120, "things": 3}
        said = {"concepts": [{"task": "t3", "associations": ["fruit", "farm"], "idea": "A melon farm estate.",
                              "parts": ["the farmhouse: its studio"], "fill": ["plant"]}],
                "content_switch": {"switched": False}, "zones": [{"kind": "studio", "name": "WaterMelon", "why": "w",
                                                                  "task": "t3", "form": "u_court"}]}
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder, said, [task])
            keeper.world()
            keeper.grow("2026-11-18T10:00:00+00:00")
            tasks = [r.task for r in keeper.models.asked]
            self.assertLess(tasks.index(pw.TASK_CONCEIVE), tasks.index(pw.TASK), "conceived before it is built")
            build = json.loads(next(r for r in keeper.models.asked if r.task == pw.TASK).messages[0]["content"])
            self.assertEqual(build["concepts"][0]["idea"], "A melon farm estate.", "the build given the concept")
            world = keeper.load()
            zone = next(z for z in world["zones"] if z.get("task") == "t3" and not z.get("plot"))
            self.assertEqual(zone["concept"]["idea"], "A melon farm estate.")
            self.assertEqual(zone["form"], "u_court", "half open")
        from personalized_agent.common import modules
        self.assertEqual(modules.module_of(pw.TASK_CONCEIVE), "play_world")

    def test_an_area_full_the_next_region_begins_another_apart(self) -> None:
        from personalized_agent.agent import playregions as pr
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder, {})
            world = keeper.world()
            style = pr.chosen({"biome": "meadow", "layout": "village", "palette": "day", "architecture": "stone",
                               "theme": "quiet", "landmark": "fountain"})
            for n in range(pr.AREA_REGIONS):
                pr.open_region(world, style, f"Land {n}", "w", "2026-11-18", None, pw.CELL_W, pw.CELL_H)
            areas = [pr.area_of(r) for r in world["regions"]]
            self.assertEqual(areas.count("a1"), pr.AREA_REGIONS)
            newest = world["regions"][-1]
            self.assertEqual((newest["area"], newest["from"]), ("a2", None), "its own area, joined to none")
            first = {tuple(r["cell"]) for r in world["regions"] if pr.area_of(r) == "a1"}
            self.assertFalse(any(max(abs(newest["cell"][0] - c[0]), abs(newest["cell"][1] - c[1])) <= 1 for c in first))
        page = (WEB / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("function playAreaWorld", page)
        self.assertIn("gate: true", (WEB / "play-act.js").read_text(encoding="utf-8"))

    def test_a_skyline_on_a_lakeshore_stands_as_a_fountain_and_the_look_is_paged(self) -> None:
        from personalized_agent.agent import playregions as pr
        said = {"biome": "lakeside", "layout": "village", "palette": "day", "architecture": "stone", "theme": "quiet",
                "landmark": "skyline"}
        self.assertEqual(pr.chosen(said)["landmark"], "fountain")
        self.assertEqual(pr.chosen(dict(said, biome="city"))["landmark"], "skyline")
        page = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("function playPages", page)
        self.assertNotIn("`${text.slice(0, PLAY_SAY_MOST - 1)}…`", page, "never cut")


class ThingsByThemeTests(unittest.TestCase):
    """2026-11-17: "告诉模型现在已有哪些主题。模型可以根据主题找每个image(每个有description), 然后根据description可以打开多个图片
    比较"; the themes asked for that day each there."""

    def test_every_theme_asked_for_has_its_things_each_with_its_words(self) -> None:
        from personalized_agent.agent import playcatalogue as pc
        themes = pc.by_theme()
        for theme in ("graveyard", "haunted", "christmas", "newyear", "halloween", "valentine", "funfair", "botanical",
                      "zoo", "space", "scifi", "underwater", "skycity", "ruins", "toytown"):
            self.assertIn(theme, themes)
            self.assertTrue(themes[theme]["things"], theme)
        page = (WEB / "play-regions.js").read_text(encoding="utf-8")
        for name, one in pc.catalogue()["things"].items():
            self.assertTrue(one["what"], name)
            self.assertIn(f"{name}: [", page, f"{name} is the page's own")
            self.assertIn(name, pw.ITEM_KINDS, "every thing a kind the model may place")
            self.assertTrue((pc.PLAY / f"{one['spec'][0]}.png").is_file())

    def test_the_themes_first_then_only_their_things(self) -> None:
        """2026-11-17: "每次是先给topic和topic的description, 然后模型选topic再给这个topic的description。如果太多还可以topic上
        再有hierarchy"."""
        from personalized_agent.agent import playcatalogue as pc
        tree = pc.theme_tree()
        self.assertIn("graveyard", tree["fantasy and the spooky"])
        self.assertNotIn("things", json.dumps(tree), "the themes alone, no things")
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Models({"themes": ["graveyard", "not a theme", "halloween"]}),
                                  SimpleNamespace(get=lambda key, default=None: default))
            world = keeper.world()
            chosen = keeper._themes("2026-10-25", "2026-11-01", "Halloween party", world)
            self.assertEqual(chosen[:2], ["graveyard", "halloween"])
            self.assertIn(world["regions"][0]["biome"], chosen, "the lands' own too")
            self.assertEqual(keeper.models.asked[-1].task, "play_world_themes")
            asked = json.loads(keeper.models.asked[-1].messages[0]["content"])
            self.assertIn("THEMES", asked)
            self.assertNotIn("hauntedStone", json.dumps(asked), "no things before a theme is chosen")
        given = pc.by_theme(chosen)
        self.assertEqual(set(given), set(chosen) & set(pc.catalogue()["themes"]))

    def test_unsure_it_is_shown_them_side_by_side_and_its_choice_is_kept(self) -> None:
        from personalized_agent.agent import playcatalogue as pc
        self.assertEqual(pc.compare_picture(["hauntedStone", "mausoleum"])["media_type"], "image/png")
        with tempfile.TemporaryDirectory() as folder:
            keeper = pw.PlayWorld(Path(folder), Models({"picks": {"1": "mausoleum"}}),
                                  SimpleNamespace(get=lambda key, default=None: default))
            said = {"items": [{"zone": "z1", "compare": ["hauntedStone", "vault", "mausoleum"], "name": "Old tomb"},
                              {"zone": "z1", "kind": "lantern"}]}
            keeper._pick(said)
            self.assertEqual(said["items"][0]["kind"], "mausoleum")
            request = keeper.models.asked[-1]
            self.assertEqual(request.task, "play_world_pick")
            self.assertTrue(any(block.get("type") == "image" for block in request.messages[0]["content"]))
        self.assertIn("play_world_pick", __import__("personalized_agent.common.modules", fromlist=["x"]).TASK_MODULE)


class ThePageTests(unittest.TestCase):

    def test_every_function_the_page_calls_is_there(self) -> None:
        """2026-11-17: "怎么还是一片黑" - a function cut away with another left every block undrawn."""
        import re
        from support import ROOT
        web = ROOT / "personalized_agent" / "api" / "web"
        source = "".join((web / name).read_text(encoding="utf-8")
                         for name in ("play.js", "play-regions.js", "play-sky.js", "play-sound.js", "play-act.js"))
        defined = set(re.findall(r"function (play\w+)\s*\(", source)) | set(re.findall(r"(?:const|let) (play\w+)\s*=", source))
        called = set(re.findall(r"(play[A-Z]\w+)\s*\(", source))
        self.assertEqual(sorted(called - defined), [])

    def test_portals_rides_and_things_to_do(self) -> None:
        """2026-11-17: "允许有传送门（类似于冒险岛的光圈，可以用几种不同的传送门）"; "可以搭乘飞机，天空之城的船，摆渡，还有rotate的
        火车"; "椅子可以坐下，可以手动开关灯/火把 ... 都要有动画"; "水里可以钓鱼，游泳"; "加一些飞鸟，小动物"; "牌匾（标志）也有几种
        style"."""
        from support import ROOT
        web = ROOT / "personalized_agent" / "api" / "web"
        self.assertIn('<script src="/play-act.js"></script>', (web / "notes.html").read_text(encoding="utf-8"), "loaded")
        act = (web / "play-act.js").read_text(encoding="utf-8")
        for look in ('"pad"', '"ring"', '"arch"', '"fairy"', '"light"'):
            self.assertIn(look, act)
        for kind in ("train", "ferry", "airship", "plane"):
            self.assertIn(f'kind: "{kind}"', act)
        for name in ("function playRide", "function playTeleport", "PLAY_SEATS", "function playLightToggle",
                     "function playFishPress", "function playCrittersStep", "play.birds"):
            self.assertIn(name, act)
        page = (web / "play.js").read_text(encoding="utf-8")
        for style in pw.SIGN_STYLES:
            self.assertIn(f"  {style}: {{ post:", page)
        self.assertIn('if (piece.startsWith("water") && !piece.startsWith("waterFall")) return false;', page, "swum in")
        self.assertIn("play.lightsOff.has", (web / "play-sky.js").read_text(encoding="utf-8"), "a light put out stays dark")

    def test_its_tiles_are_kenneys_cc0_and_said_so(self) -> None:
        from support import ROOT
        play = ROOT / "personalized_agent" / "api" / "web" / "play"
        note = (play / "ASSETS.txt").read_text(encoding="utf-8")
        self.assertIn("CC0", note)
        # 2026-11-17: "检查一下安装包里不包含任何不是CC0的东西和你不要的resources" - every file here said in the note (all of
        # it CC0, or made for the application), and every one of them used.
        web = ROOT / "personalized_agent" / "api" / "web"
        code = "".join((web / name).read_text(encoding="utf-8") for name in ("play.js", "play-regions.js", "play-sound.js"))
        for path in sorted(play.rglob("*")):
            if path.is_dir() or path.name == "ASSETS.txt":
                continue
            said = path.name if path.parent == play else f"{path.parent.name}/"
            self.assertIn(said if path.parent == play else path.parent.name + "/", note, f"said in the note: {path.name}")
            if path.parent.name == "music":
                self.assertIn(f'"{path.name}"', code, f"a track used: {path.name}")
            elif path.parent.name == "sfx":
                self.assertIn(path.stem, code, f"a sound used: {path.name}")
            elif path.name == "catalogue.json":           # the things by theme, read by agent/playcatalogue.py
                self.assertIn("catalogue.json", (ROOT / "personalized_agent" / "agent" / "playcatalogue.py").read_text(encoding="utf-8"))
            else:
                self.assertIn(f"/play/{path.name}", code, f"a sheet used: {path.name}")
        self.assertFalse((play / "characters.png").exists(), "the old walker's sheet gone")
        self.assertFalse(any("cave-loop" in one.name for one in play.rglob("*")), "no dark track")
        source = (ROOT / "personalized_agent" / "api" / "web" / "play.js").read_text(encoding="utf-8")
        regions = (ROOT / "personalized_agent" / "api" / "web" / "play-regions.js").read_text(encoding="utf-8")
        hangings = (play / "hangings.json").read_text(encoding="utf-8")
        for kind, said in pw.ITEM_KINDS.items():
            # A thing of the catalogue is drawn as the scenery of its name (play.js playThing); a theme's thing to hang
            # from its shape (play/hangings.json, play.js playThemeHangingDraw).
            if said.get("theme"):
                self.assertIn(f'["{kind}", ', hangings, kind)
                continue
            if said.get("made"):                         # made by the agent: drawn from its picture in the library
                continue
            self.assertIn(f"{kind}:", regions if said.get("thing") else source, f"every kind the model may choose is drawn: {kind}")


def _js_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth, at = 0, source.index("{", start)
    for i in range(at, len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[start:i + 1]
    raise ValueError(name)


class WalksFromTheFirstMomentTests(unittest.TestCase):
    """2026-11-18: "现在你的play panel, 会出现刚打开的时候没有焦点（可能是还没load完), 不能运动的问题"."""

    def test_a_key_while_loading_never_leaves_it_stuck(self) -> None:
        source = (WEB / "play.js").read_text(encoding="utf-8")
        step, run, opened = (_js_function(source, name) for name in ("playStep", "playRun", "playOpen"))
        self.assertIn("if (!play.map) return false;", step, "no step before the world is laid")
        self.assertIn("catch (err)", run)
        self.assertIn("playRunning = false;", run[run.index("catch (err)"):], "a frame that throws ends the run")
        self.assertLess(opened.index("play.canvas.focus("), opened.index("await playLoadSheets()"), "the keys its at once")


class AWorldMapNotRectanglesTests(unittest.TestCase):
    """2026-11-18: "你现在layout还不是很行，没有脱离矩形" (a world map of islands for a picture); "每个房间里的东西密度太小了 ...
    personal agent这个project, 基本里面空余空间要小于50%"; "你的造景我还是很难在小地图上一眼看出哪个是personal agent"."""

    REGION = {"id": "r7", "ox": 0, "oy": 0, "name": "Testing Isle"}

    def test_a_land_has_its_own_outline_and_never_loses_a_place_for_it(self) -> None:
        from personalized_agent.agent import playplaces as places
        b = places.bounds(self.REGION)
        corner = (b[0], b[1], b[0] + 4, b[1] + 4)
        middle = ((b[0] + b[2]) // 2 - 2, (b[1] + b[3]) // 2 - 2, (b[0] + b[2]) // 2 + 2, (b[1] + b[3]) // 2 + 2)
        self.assertFalse(places.within_shape(self.REGION, corner), "its corners off")
        self.assertTrue(places.within_shape(self.REGION, middle))
        other = dict(self.REGION, id="r8")
        reach = [round(places.shape_limit(one, a / 10), 3) for one in (self.REGION, other) for a in range(0, 63, 9)]
        self.assertNotEqual(reach[:7], reach[7:], "each land its own shape")
        world = {"zones": [], "regions": [self.REGION]}
        spot = places.find_spot(world, self.REGION, "home", 11, 6, places.centre(b), "z1")
        self.assertTrue(places.within_shape(self.REGION, places.footprint("home", *spot, 11, 6)))
        # Its outline filled, a place still stands - anywhere in its land.
        world["zones"] = [{"kind": "home", "x": x, "y": y, "w": 11, "h": 6}
                          for x in range(b[0], b[2], 16) for y in range(b[1], b[3], 16)
                          if places.within_shape(self.REGION, places.footprint("home", x, y, 11, 6))]
        self.assertIsNotNone(places.find_spot(world, self.REGION, "home", 11, 6, places.centre(b), "z99")
                             or places.find_spot({"zones": [], "regions": []}, self.REGION, "home", 11, 6, (b[0], b[1]), "z99"))

    def _thick_world(self, fill=None):
        # A great work's lab as large as Personal Agent's (14 x 23, a wall across it): walkable rows fill it to its share.
        zone = {"id": "z1", "kind": "lab", "x": 0, "y": 0, "w": 14, "h": 23, "door": 8, "task": "t1"}
        items = [{"id": f"i{n}", "zone": "z1", "kind": kind, "x": 1 + 2 * n, "y": 3, "name": name, "why": "Its reason.",
                  "made": "2026-11-01", "picture": "", "group": group}
                 for n, (kind, name, group) in enumerate([("computer", "Test rig", "g1"), ("computer", "Test rig", "g1"),
                                                          ("bookshelf", "Notes", "g2"), ("candle", "A light", "g3")])]
        fill = pw.fill_of(29.8) if fill is None else fill
        return {"zones": [zone], "items": items, "regions": [], "tasks": {"t1": {"minutes": 9000, "fill": fill}}}, zone

    def test_how_complex_a_task_is_and_how_full_its_places(self) -> None:
        # 2026-11-18: "这个任务的状态数 ÷ 3 + 资料数 这个公式太简单了，比如context switch的数量，deliverable的数量"; "personal agent
        # 的task, 算出来的空地比例要小于一半".
        great = {"hours": 162, "switches": 118, "days": 38, "places": 386, "deliverables": 113, "pieces": 138}
        leisure = {"hours": 100, "switches": 75, "days": 38, "places": 568, "deliverables": 1, "pieces": 77}
        hour = {"hours": 1, "switches": 1, "days": 1, "places": 3, "deliverables": 0, "pieces": 1}
        self.assertLess(1 - pw.fill_of(pw.complexity_of(great)), 0.5, "the great work's places less than half free")
        self.assertGreater(pw.complexity_of(great), pw.complexity_of(leisure), "what it delivers weighs most")
        self.assertGreater(1 - pw.fill_of(pw.complexity_of(hour)), 0.8, "an hour's task nearly bare")
        more = dict(hour, switches=40)
        self.assertGreater(pw.complexity_of(more), pw.complexity_of(hour), "switching into it counts")
        # Measured from the run tasks: the times the user came to it from something else.

        class Rows:
            def query(self, _sql, _params=()):
                return [{"project_id": p, "minutes": 30, "starts_at": f"2026-11-0{d}T10:00", "places": '{"a": 1}',
                         "resources": '[{"kind": "code", "where": "x.py"}]' if p == "A" else "[]"}
                        for p, d in (("A", 1), ("A", 1), ("B", 2), ("A", 3), ("B", 3), ("A", 4))]
        measured = pw.project_measures(Rows())
        self.assertEqual(measured["A"]["switches"], 3)
        self.assertEqual(measured["A"]["days"], 3)
        self.assertEqual(measured["A"]["deliverables"], 1.0, "one file, however often")
        self.assertEqual(measured["A"]["pieces"], 4)
        source = Path(pw.__file__).read_text(encoding="utf-8")
        self.assertIn('"measures": measures, "complexity": complexity, "fill": fill_of(complexity)', source)

    def test_a_great_works_places_filled_with_a_few_large_things_and_ways_to_walk(self) -> None:
        # 2026-11-18: "你要把布局补满，可以是做一个item但是那个Item很大"; "每次用一个Principal做design".
        world, zone = self._thick_world()
        keeper = pw.PlayWorld.__new__(pw.PlayWorld)
        laid = keeper._thicken(world, "2026-11-18")
        floor = keeper._floor_tiles(zone)
        self.assertGreater(laid, 0)
        self.assertLess(keeper._free_share(world, zone), 0.5, "less than half of it free")
        new = [one for one in world["items"] if one.get("carried")]
        # 2026-11-18: "对这种大的东西，一定要细粒度": nothing but a picture of theirs made large.
        self.assertFalse(any(one.get("scale") for one in new if not one.get("picture")), "no small thing made coarse")
        self.assertEqual(pw.item_size({"kind": "computer", "scale": 4}), pw.item_size({"kind": "computer"}))
        self.assertEqual({one["kind"] for one in new}, {"computer", "bookshelf"}, "only what the model laid there")
        self.assertNotIn("candle", {one["kind"] for one in new}, "a small thing says no more made large")
        self.assertEqual({one["principle"] for one in new}, {"contrast"}, "one principle to the place")
        self.assertFalse(any(one["x"] <= 9 < one["x"] + pw.item_size(one)[0] for one in new), "the door's way clear")
        covered = set()
        for one in world["items"]:
            covered |= keeper._tiles_of(one)
        free = floor - covered
        reached, todo = set(), [(8, max(y for _x, y in floor))]
        while todo:
            at = todo.pop()
            if at in reached or at not in free:
                continue
            reached.add(at)
            todo += [(at[0] + 1, at[1]), (at[0] - 1, at[1]), (at[0], at[1] + 1), (at[0], at[1] - 1)]
        for one in new:
            beside = {(x + dx, y + dy) for (x, y) in keeper._tiles_of(one) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))}
            self.assertTrue(beside & reached, f"{one['kind']} at {one['x']},{one['y']} reached")
        self.assertEqual(keeper._thicken(world, "2026-11-18"), 0, "full enough: nothing more")
        # 2026-11-18: "你现在如果是开放式房间，不要中间留2个门的位置，很突兀": open in front, no door's way through it -
        # and every thing still reached from its open front.
        world, zone = self._thick_world()
        zone["form"] = "open_front"
        keeper._thicken(world, "2026-11-18")
        new = [one for one in world["items"] if one.get("carried")]
        self.assertTrue(any(one["x"] in (8, 9) for one in new), "things where a door's way would have been")
        self.assertFalse(keeper._has_door(zone))
        # Consistency: the one most telling design, again.
        world, zone = self._thick_world()
        for one in world["items"]:
            one["principle"] = "consistency"
        keeper._thicken(world, "2026-11-18")
        self.assertEqual(len({one["kind"] for one in world["items"] if one.get("carried")}), 1)
        # A small task's place: as little as its complexity asks - most of it free.
        world, zone = self._thick_world(fill=pw.fill_of(3.1))
        keeper._thicken(world, "2026-11-18")
        self.assertGreater(keeper._free_share(world, zone), 0.8)
        source = Path(pw.__file__).read_text(encoding="utf-8")
        self.assertIn('for one in world["items"] if not one.get("carried")][-80:]', source)
        self.assertIn('and not one.get("carried")]', source)
        self.assertIn("items_added += self._thicken(world, at)", source)
        self.assertIn('"scale": 1 to 6, "principle": "contrast" or "consistency"', pw.SYSTEM)
        page = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("function playItemSize(", page)
        self.assertIn("playItemSize(item);", (WEB / "play-regions.js").read_text(encoding="utf-8"), "as large in the way")

    def test_no_picture_twice_none_alike(self) -> None:
        # 2026-11-18: "我看你放了很多同样的terminal截图，又不美观本质上内容也很同质".
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "pictures").mkdir()
            dark = Image.new("RGB", (120, 80), (20, 22, 26))
            for x in range(10, 110, 7):
                for y in range(10, 70):
                    dark.putpixel((x, y), (200, 200, 200))
            dark.save(folder / "a.png")
            dark.save(folder / "pictures" / "i1.jpg")
            bright = Image.new("RGB", (120, 80))
            for x in range(120):
                for y in range(80):
                    bright.putpixel((x, y), ((x * 2) % 256, (y * 3) % 256, 255 - x))
            bright.save(folder / "b.png")
            keeper = pw.PlayWorld.__new__(pw.PlayWorld)
            keeper.folder = folder
            zone = {"id": "z1"}
            world = {"items": [{"id": "i1", "zone": "z1", "kind": "frame", "picture": "i1.jpg", "made": "1"}]}
            self.assertFalse(keeper._picture_fits(world, zone, folder / "a.png"), "the same picture again")
            self.assertTrue(keeper._picture_fits(world, zone, folder / "b.png"))
            self.assertLess(pw.picture_print(folder / "a.png")[1], pw.PLAIN_COLOUR, "a terminal is plain")
            self.assertGreater(pw.picture_print(folder / "b.png")[1], pw.PLAIN_COLOUR)
            # The same picture framed again and again by the code is let go; the model's own kept.
            world["items"] += [{"id": f"i{n}", "zone": "z1", "kind": "frame", "picture": "i1.jpg", "made": str(n),
                                "from": "picture: the terminal"} for n in (2, 3)]
            self.assertEqual(keeper._drop_repeated_pictures(world), 2)
            self.assertEqual([one["id"] for one in world["items"]], ["i1"])
        source = Path(pw.__file__).read_text(encoding="utf-8")
        self.assertIn('if want <= 0 or pid in framed or str(picture.get("what", "")) in framed:', source,
                      "a task's picture framed once, by its id")

    def test_the_map_pressed_looks_there_and_a_step_comes_back(self) -> None:
        # 2026-11-18: "你现在小地图允许我用鼠标点，view会跳到我小地图上看的那个位置 ... 我操作上下左右的时候又跳回agent视角";
        # "agent view用黄色边框"; "你的小地图，不要标出block, 就正常色块".
        page = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn('play.mini.addEventListener("pointerdown"', page)
        # 2026-11-19: "点击鼠标中键之后就变成可以移动上下作用view了": the left button alone, the middle one's scrolling kept off.
        self.assertIn("if (event.button !== 0) return;", page)
        self.assertIn('play.mini.addEventListener("mousedown", (event) => { if (event.button === 1) event.preventDefault(); });', page)
        self.assertIn("play.lookAt = play.miniBase.back(mx, my);", page)
        self.assertIn("const middle = play.lookAt || [play.me.x, play.me.y];", page, "the view where it was pressed")
        self.assertIn("play.lookAt = null;                          // a step", page, "an arrow key brings it back")
        self.assertIn('play.mini.addEventListener("dblclick", () => playMiniLarger());', page, "larger or smaller: twice")
        draw = _js_function(page, "playMiniDraw")
        self.assertIn('c.strokeStyle = "#ffd54a"', draw, "the agent's view in yellow")
        base = _js_function(page, "playMiniBase")
        self.assertNotIn("playMiniGrounds(c", base, "nothing drawn over the grounds")
        self.assertIn("playMiniLabels(c, at);", base, "the names' boards kept")
        self.assertNotIn("strokeRect", _js_function(page, "playMiniLabels"), "no box round a ground")

    def test_the_coast_the_sea_and_the_main_work_on_the_map(self) -> None:
        regions = (WEB / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("function playLandReach(", regions)
        self.assertIn('ground[i] = under[i] = deep ? (playHash(x, y, 5) < 0.5 ? "seaDeep"', regions, "a sea, not the dark")
        self.assertIn("!index.joined.has(", regions, "a strait between lands not joined by their days")
        self.assertIn("for (const one of world.commutes || [])", regions)
        page = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("playMiniLabels(c, at);", page)
        main = _js_function(page, "playMainWork")
        self.assertIn("play.world.projects", main, "a project's tasks together")
        self.assertIn("book[t] && book[t].minutes", main, "by the time given to it")
        self.assertIn("★ ${main.name}", _js_function(page, "playMiniLabels"))


class EveryThingToDoShownTests(unittest.TestCase):
    """2026-11-18: "所有可交互的东西，agent在附近都要提示可以交互"."""

    def test_each_thing_e_does_has_its_hint_over_the_walker(self) -> None:
        source = (WEB / "play-act.js").read_text(encoding="utf-8")
        hint = _js_function(source, "playActHint")
        for words in ("Reel in", "Go to another area", "Go through the portal", "Ride", "Sit", "Light it", "Put the light out",
                      "Fish", "See the picture", "Look"):
            self.assertIn(f'"{words}"', hint)
        zh = json.loads((WEB / "lang" / "zh.json").read_text(encoding="utf-8"))
        for words in ("Reel in", "Go to another area", "Go through the portal", "Ride", "Sit", "Light it", "Put the light out",
                      "Fish", "See the picture", "Look"):
            self.assertIn(words, zh, f"said in Chinese too: {words}")
        self.assertIn("playActHintDraw(size);", _js_function(source, "playActOver"))
        self.assertIn("play.said && performance.now() < play.said.until", _js_function(source, "playActHintDraw"),
                      "not over what is being said")


class LinesOnTheirOwnGroundTests(unittest.TestCase):
    """2026-11-18: "你现在怎么轮船在路上开"; "你现在的火车铁轨也非常不行"; "火车铁轨转弯不够smooth"."""

    def _run(self, body: str) -> dict:
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node is not here")
        source = (WEB / "play-act.js").read_text(encoding="utf-8")
        script = "".join(_js_function(source, name) for name in ("playWaterRoute", "playClear", "playPulled", "playRounded")) + body
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_ferry_goes_on_the_water_or_not_at_all(self) -> None:
        # A lake shaped like an L (x 0-19 at y 0-4, then x 15-19 down to y 19); land elsewhere.
        said = self._run("""
            const lake = (x, y) => (y >= 0 && y < 5 && x >= 0 && x < 20) || (x >= 15 && x < 20 && y >= 0 && y < 20);
            const playIsWater = lake;
            const route = playWaterRoute([1, 2], [17, 18]);
            const none = playWaterRoute([1, 2], [5, 15]);
            process.stdout.write(JSON.stringify({ wet: route.every(([x, y]) => lake(x, y)), ends: [route[0], route[route.length - 1]], none }));""")
        self.assertTrue(said["wet"], "every step on the water")
        self.assertEqual(said["ends"], [[1, 2], [17, 18]])
        self.assertIsNone(said["none"], "no water between: no way")
        source = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn('if (!leg && afloat) { line.none = true; break; }', source, "no water (or sky) between: none")
        # 2026-11-18: "你每个交通工具要有能运动的地方，比如轮船只能在水面，天空之船只能在云端".
        self.assertIn('line.kind === "airship" ? (x, y) => playIsSky(x, y)', source, "the airship in the sky only")
        self.assertIn("playSkyNear(one.heart[0], one.heart[1], one.region, side)", source)
        self.assertIn("playShoreNear(one.heart[0], one.heart[1], one.region)", source)

    def test_the_track_turns_in_curves_and_keeps_off_what_is_in_the_way(self) -> None:
        # A tile route round a block (x 5-14, y 5-14): east along y=3, then south along x=16 - stairs at first.
        said = self._run("""
            const ok = (x, y) => !(x >= 5 && x < 15 && y >= 5 && y < 15);
            const tiles = [];
            for (let x = 0; x <= 16; x += 1) tiles.push([x + 0.5, 3.5]);
            for (let y = 4; y <= 20; y += 1) tiles.push([16.5, y + 0.5]);
            const pulled = playPulled(tiles, ok), round = playRounded(pulled, ok);
            let most = 0;
            for (let i = 1; i < round.length - 1; i += 1) {
              const a = Math.atan2(round[i][1] - round[i - 1][1], round[i][0] - round[i - 1][0]);
              const b = Math.atan2(round[i + 1][1] - round[i][1], round[i + 1][0] - round[i][0]);
              let d = Math.abs(b - a); if (d > Math.PI) d = 2 * Math.PI - d; most = Math.max(most, d);
            }
            process.stdout.write(JSON.stringify({ pulled: pulled.length, points: round.length, mostTurn: most * 180 / Math.PI,
              clear: round.every(([x, y]) => ok(Math.floor(x), Math.floor(y))) }));""")
        self.assertLessEqual(said["pulled"], 4, "the stairs of tiles pulled straight")
        self.assertGreater(said["points"], said["pulled"], "the corner made a curve")
        self.assertLess(said["mostTurn"], 30, "no sharp turn")
        self.assertTrue(said["clear"], "never over the block")
        source = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn('if (line.kind === "train") playTrackDraw(line, size);', source)
        self.assertIn("line.stopAt = line.stops.map((_one, i) => cum[starts[i]]);", source, "each stop where it is along the way")

    def test_words_kept_whole_on_a_line(self) -> None:
        # 2026-11-18: "你现在英文显示怎么老把一个词拆成2行显示 在look的时候".
        source = (WEB / "play.js").read_text(encoding="utf-8")
        said = self._run(_js_function(source, "playWrapped") + """
            const ctx = { measureText: (t) => ({ width: [...t].reduce((s, c) => s + (/[\\u2e80-\\u9fff]/.test(c) ? 14 : 7), 0) }) };
            const english = "You spent two weeks on the verification mails, so a returned letter lies on the desk.";
            const lines = playWrapped(ctx, english, 140);
            const chinese = playWrapped(ctx, "你花了两周处理验证邮件，所以桌上放着一封退回的信。", 140);
            const long = playWrapped(ctx, "Supercalifragilisticexpialidocious", 70);
            process.stdout.write(JSON.stringify({ lines, joined: lines.join(""), english, chinese, long }));""")
        words = set(said["english"].split())
        for line in said["lines"]:
            for word in line.split():
                self.assertIn(word, words, f"a word broken across lines: {line!r}")
        self.assertEqual(said["joined"], said["english"], "every letter kept, in order")
        self.assertGreater(len(said["chinese"]), 1, "Chinese wrapped between characters")
        self.assertEqual("".join(said["long"]), "Supercalifragilisticexpialidocious", "a word longer than a line broken within")

    def test_a_long_straight_track_is_drawn_when_both_its_ends_are_out_of_sight(self) -> None:
        # 2026-11-18: "你现在的铁轨，有的时候踩上去就不显示了".
        source = (WEB / "play-act.js").read_text(encoding="utf-8")
        script = "".join(_js_function(source, name) for name in ("playActInView", "playActView", "playActScreen", "playTrackDraw"))
        said = self._run(script + """
            let drawn = 0;
            const ctx = new Proxy({}, { get: (t, k) => (k in t ? t[k] : (k === "lineTo" ? () => { drawn += 1; } : () => {})),
                                        set: (t, k, v) => { t[k] = v; return true; } });
            const PLAY_TILE = 16;
            var play = { ctx, scale: 2, camera: { x: 100 * 32, y: 100 * 32 }, canvas: { width: 640, height: 480 } };
            // One piece from far left of the view to far right of it, through its middle.
            const line = { pts: [[40, 107], [200, 107]], cum: [0, 160], length: 160 };
            playTrackDraw(line, 32);
            process.stdout.write(JSON.stringify({ drawn }));""")
        self.assertGreater(said["drawn"], 0, "the piece crossing the view is drawn")

    def test_the_train_keeps_to_its_rails_and_turns_smoothly(self) -> None:
        # 2026-11-18: "你现在的火车旋转很不行"; "你现在的火车根本没有贴在铁轨上".
        source = (WEB / "play-act.js").read_text(encoding="utf-8")
        script = "".join(_js_function(source, name) for name in ("playLinePoint", "playLineHeading", "playTrainCars"))
        consts = source[source.index("const PLAY_CARS"):source.index("\n", source.index("const PLAY_CARS"))]
        said = self._run(consts + script + """
            const ok = (x, y) => !(x >= 5 && x < 15 && y >= 5 && y < 15);
            const tiles = [];
            for (let x = 0; x <= 16; x += 1) tiles.push([x + 0.5, 3.5]);
            for (let y = 4; y <= 20; y += 1) tiles.push([16.5, y + 0.5]);
            const pts = playRounded(playPulled(tiles, ok), ok), cum = [0];
            for (let i = 1; i < pts.length; i += 1) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
            let far = 0, turn = 0, gaps = [], inside = true;
            // Round a ring too, its last point its first (a loop's way ends where it began).
            const ring = Array.from({ length: 61 }, (_x, i) => [30 + 8 * Math.cos(i * Math.PI / 30), 30 + 8 * Math.sin(i * Math.PI / 30)]);
            const ringCum = [0];
            for (let i = 1; i < ring.length; i += 1) ringCum.push(ringCum[i - 1] + Math.hypot(ring[i][0] - ring[i - 1][0], ring[i][1] - ring[i - 1][1]));
            for (const loop of [false, true]) {
              const line = loop ? { pts: ring, cum: ringCum, length: ringCum[ringCum.length - 1], loop } : { pts, cum, length: cum[cum.length - 1], loop };
              const track = loop ? ring : pts;
              const off = (x, y) => {
                let best = Infinity;
                for (let i = 1; i < track.length; i += 1) {
                  const [ax, ay] = track[i - 1], [bx, by] = track[i], dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy || 1;
                  const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / L2));
                  best = Math.min(best, Math.hypot(x - ax - dx * t, y - ay - dy * t));
                }
                return best;
              };
              let was = null;
              for (let d = 0; d < 3 * line.length; d += 0.15) {
                const cars = playTrainCars(line, d);
                const heads = cars.map((car) => {
                  const [x, y] = playLinePoint(line, car.u);
                  far = Math.max(far, off(x, y));
                  if (!loop && (car.u < 0 || car.u > line.length)) inside = false;
                  const f = playLinePoint(line, car.u + car.dir * 0.85), b = playLinePoint(line, car.u - car.dir * 0.85);
                  return [Math.atan2(f[1] - b[1], f[0] - b[0]), x, y];
                });
                for (let k = 1; k < heads.length; k += 1) gaps.push(Math.hypot(heads[k][1] - heads[k - 1][1], heads[k][2] - heads[k - 1][2]));
                if (was && cars[0].dir === was.dir) {
                  heads.forEach(([h], k) => { let t = Math.abs(h - was.heads[k][0]); if (t > Math.PI) t = 2 * Math.PI - t; turn = Math.max(turn, t); });
                }
                was = { dir: cars[0].dir, heads };
              }
            }
            process.stdout.write(JSON.stringify({ far, turn: turn * 180 / Math.PI, least: Math.min(...gaps), most: Math.max(...gaps), inside }));""")
        self.assertLess(said["far"], 0.02, "every car on the rails")
        self.assertLess(said["turn"], 6, "turned a little at a time, never in a jump")
        self.assertGreater(said["least"], 1.6, "the cars apart, round the curve too")
        self.assertLessEqual(said["most"], 2.06)
        self.assertTrue(said["inside"], "there and back: never past the line's ends")
        self.assertIn("playTrainDraw(line, cars, size)", source)
        self.assertNotIn("sy - Math.sin(heading) * back - size * 0.5", source, "no longer half a tile over its rails")


class FewAnimalsTests(unittest.TestCase):
    """2026-11-18: "小动物可以不要加这么多，减少对资源的消耗"."""

    def test_two_at_most_and_only_a_moving_one_asks_for_frames(self) -> None:
        source = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("const PLAY_CRITTERS_MOST = 2;", source)
        self.assertIn("(play.critters || []).some((one) => one.moving && playActInView(one.x, one.y, 1))", source)

    def test_no_animal_stands_still(self) -> None:
        # 2026-11-18: "所以这里这么多动物只是固定的吗"; "要是动画item放上去不动，那你还是别放会动的item".
        from personalized_agent.agent import playcatalogue as pc
        self.assertTrue(pw.alive("dog") and pw.alive("rabbit") and pw.alive("deer"))
        # "围栏里的狮虎和海底的鱼 ... 这些不要移除"; statues, robots, wisps untouched.
        self.assertFalse(pw.alive("lion") or pw.alive("clownfish") or pw.alive("giantDog") or pw.alive("metalBot3")
                         or pw.alive("wisp") or pw.alive("bench"))
        offered = " ".join(t for one in pc.by_theme().values() for t in one["things"])
        self.assertNotIn("dog:", offered)
        self.assertIn("lion:", offered)
        self.assertIn("watermelon:", offered)
        with tempfile.TemporaryDirectory() as folder:
            keeper = _world(folder)
            world = keeper.world()
            zone = keeper._add_zone(world, "study", "Den", "w", "2026-11-18")
            self.assertIsNone(keeper._add_item(world, zone, {"kind": "dog", "why": "w"}, "2026-11-18"))
            self.assertEqual(keeper._arrange(world, {"zone": zone["id"], "kind": "cat", "count": 3, "why": "w"}, "2026-11-18"), 0)
        # "这些你可以把它们变成动的画": where a land lays one, it lives - it walks about there.
        regions = (WEB / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("if (playAlive(mine.name)) { animals.push({ name: mine.name, x, y }); continue; }", regions)
        act = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("play.critters.push({ name: one.name, key, home: [one.x + 0.5, one.y + 0.8]", act)
        self.assertIn("one.tx = (one.home ? hx : one.x)", act, "walking about where it was laid")
        # "这些动物是有限制的（比如多少区域内不超过2个）".
        self.assertIn("const PLAY_ANIMALS_A_BLOCK = 2;", regions)
        self.assertIn(".slice(0, PLAY_ANIMALS_A_BLOCK)", regions)


class TheShapeOfTheDaysTests(unittest.TestCase):
    """2026-11-18: "layout整体应该反映你的daily activity structure ... 出现在同一天的task有边 ... 那个最大的点在比较中间 ... 如果是
    regular rotate, 就会有通勤公交车，火车之类"; "layout形状就要让我一眼看出我的主要task是什么"; "让model做一个cluster ...
    给他们的空间是在一起的，分成好几块但是在一起 ... 每个块有单独的名字 ... 最终进行summary of conception"."""

    def _keeper(self, folder, said=None):
        return pw.PlayWorld(Path(folder), Models(said or {}), SimpleNamespace(get=lambda key, default=None: default))

    def _days(self, first, count):
        from datetime import date, timedelta
        return [(date(2026, 9, 1) + timedelta(days=first + n)).isoformat() for n in range(count)]

    def test_the_heaviest_in_the_middle_the_tied_beside_it_a_line_between(self) -> None:
        from personalized_agent.agent import playregions as pr
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder)
            world = keeper.world()
            lands = []
            for name in ("Tide", "Kiln", "Harbour", "Orchard"):
                lands.append(pr.open_region(world, dict(pr.STARTER), name, "w", "2026-11-18", None, pw.CELL_W, pw.CELL_H))
            places = {}
            for n, (region, task) in enumerate(zip([world["regions"][0], *lands], ("t0", "t1", "t2", "t3", "t4"))):
                z = keeper._add_zone(world, "study", f"Place {n}", "w", "2026-11-18", region=region)
                z["task"] = task
                places[task] = z
            keeper._add_item(world, places["t3"], {"kind": "plant", "why": "w"}, "2026-11-18")
            world["tasks"] = {"t0": {"minutes": 60, "days": self._days(80, 2)},
                              "t1": {"minutes": 300, "days": self._days(0, 3)},
                              "t2": {"minutes": 200, "days": self._days(60, 4)},
                              "t3": {"minutes": 3000, "days": self._days(0, 40)},        # the main task, on the last land
                              "t4": {"minutes": 2000, "days": self._days(5, 20)}}       # tied to it on twenty days
            # 2026-11-18: "connection的weight是两个project overlap时间除以max(各自的work时间)": 20 days x 75 min / 3000.
            heavy = world["regions"][3]
            cell = list(heavy["cell"])
            thing = next(i for i in world["items"] if i["zone"] == places["t3"]["id"])
            before = (thing["x"] - places["t3"]["x"], thing["y"] - places["t3"]["y"])
            keeper._lay_regions(world)
            self.assertEqual(heavy["cell"], cell, "the main one stays, the middle")
            tied = world["regions"][4]
            self.assertLessEqual(max(abs(tied["cell"][0] - cell[0]), abs(tied["cell"][1] - cell[1])), 1, "the tied beside it")
            self.assertEqual(tied["from"], heavy["id"], "joined to what it is tied to")
            self.assertIsNone(heavy.get("from"), "the others' roads come to the middle")
            self.assertEqual(world["commutes"], [{"from": min(heavy["id"], tied["id"]), "to": max(heavy["id"], tied["id"]),
                                                  "tie": 0.5, "days": 20}])
            cells = [tuple(r["cell"]) for r in world["regions"]]
            self.assertEqual(len(cells), len(set(cells)), "no two on one cell")
            for region in world["regions"]:
                ox, oy = pr.origin(tuple(region["cell"]), pw.CELL_W, pw.CELL_H)
                self.assertEqual((region["ox"], region["oy"]), (ox, oy))
            self.assertEqual((thing["x"] - places["t3"]["x"], thing["y"] - places["t3"]["y"]), before, "its things with it")
            source = (WEB / "play-act.js").read_text(encoding="utf-8")
            self.assertIn("lines.push({ kind: \"train\", stops, loop: false, commute: true });", source)

    def test_a_steady_world_the_laid_stay_the_new_placed_the_middle_changed_only_when_far_outgrown(self) -> None:
        # 2026-11-18: "是的，世界稳定一点".
        from personalized_agent.agent import playregions as pr
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder)
            world = keeper.world()
            for name in ("Tide", "Kiln", "Harbour"):
                pr.open_region(world, dict(pr.STARTER), name, "w", "2026-11-18", None, pw.CELL_W, pw.CELL_H)
            for n, region in enumerate(world["regions"]):
                keeper._add_zone(world, "study", f"Place {n}", "w", "2026-11-18", region=region)["task"] = f"t{n}"
            world["tasks"] = {f"t{n}": {"minutes": m, "days": self._days(0, d)} for n, (m, d) in
                              enumerate(((100, 2), (5000, 30), (300, 10), (200, 5)))}
            keeper._lay_regions(world)
            laid = {r["id"]: list(r["cell"]) for r in world["regions"]}
            middle = world["regions"][1]["id"]
            # The ties change and a new land comes: the laid keep their cells, the new one is placed beside them.
            world["tasks"]["t0"]["days"] = self._days(0, 30)
            new = pr.open_region(world, dict(pr.STARTER), "Orchard", "w", "2026-11-18", None, pw.CELL_W, pw.CELL_H)
            keeper._add_zone(world, "study", "Place 4", "w", "2026-11-18", region=new)["task"] = "t4"
            world["tasks"]["t4"] = {"minutes": 400, "days": self._days(0, 20)}
            keeper._lay_regions(world)
            self.assertEqual({rid: list(r["cell"]) for rid, r in ((r["id"], r) for r in world["regions"]) if rid in laid}, laid,
                             "the lands laid before keep their cells")
            taken = [tuple(r["cell"]) for r in world["regions"]]
            self.assertEqual(len(taken), len(set(taken)))
            # Another grown far past the middle: the middle stays, nothing moves.
            world["tasks"]["t2"]["minutes"] = 90000
            keeper._lay_regions(world)
            self.assertEqual(world["middles"][pr.area_of(world["regions"][1])], middle)
            self.assertEqual({rid: list(r["cell"]) for rid, r in ((r["id"], r) for r in world["regions"]) if rid in laid}, laid)
            # 2026-11-18: "有新的项目，应该一个不同的区域展开 ... 和personal agent 没有交集，那就应该是B在另一个地区形成中心".
            apart = pr.open_region(world, dict(pr.STARTER), "Project B", "w", "2026-11-18", None, pw.CELL_W, pw.CELL_H)
            apart["district_of"] = "p9"
            keeper._add_zone(world, "study", "B", "w", "2026-11-18", region=apart)["task"] = "tb"
            world["tasks"]["tb"] = {"minutes": 3000, "days": self._days(100, 20)}       # on none of the others' days
            keeper._lay_regions(world)
            self.assertNotEqual(pr.area_of(apart), pr.area_of(world["regions"][1]), "an area of its own")
            self.assertEqual(world["middles"][pr.area_of(apart)], apart["id"], "its middle")
            self.assertIsNone(apart.get("from"), "no road to the others: their gates")
            near = {(x + dx, y + dy) for r in world["regions"] if r is not apart for x, y in pr.cells_of(r)
                    for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            self.assertFalse(set(pr.cells_of(apart)) & near, "apart from the others")

    def test_one_project_of_two_tasks_is_one_district_its_blocks_side_by_side(self) -> None:
        from personalized_agent.agent import playplaces as pp
        said = {"projects": [{"name": "Melon Works", "tasks": ["t1", "t2"], "summary": "A melon farm worked by robots.",
                              "blocks": [{"task": "t1", "name": "Melon Fields", "idea": "Rows of melons ripening.",
                                          "associations": ["melon"], "parts": [], "fill": ["crops"]},
                                         {"task": "t2", "name": "Robot Barn", "idea": "Robots that tend the rows.",
                                          "associations": ["robot"], "parts": [], "fill": []}]}]}
        tasks = [{"id": "t1", "title": "Melon App", "summary": "", "weight": 120 * 60},
                 {"id": "t2", "title": "Helper Agent", "summary": "", "weight": 60 * 60}]
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder, said)
            world = keeper.world()
            blocks = keeper._conceive(world, tasks, [], "lived", "2026-11-01", "2026-11-18")
            self.assertEqual([b["name"] for b in blocks], ["Melon Fields", "Robot Barn"], "a block each, its own name")
            project = world["projects"][0]
            self.assertEqual((project["name"], project["tasks"], project["summary"]),
                             ("Melon Works", ["t1", "t2"], "A melon farm worked by robots."), "the concepts joined")
            for one in tasks:
                z = keeper._add_zone(world, "studio", one["title"], "w", "2026-11-18")
                z.update(task=one["id"], task_title=one["title"])
            keeper._important_info = {one["id"]: one for one in tasks}
            keeper._plots(world, "2026-11-18")
            plots = [z for z in world["zones"] if z.get("plot")]
            self.assertEqual(len(plots), 2)
            district = next(r for r in world["regions"] if r.get("district_of") == project["id"])
            self.assertEqual({z["region"] for z in plots}, {district["id"]}, "one ground for all of it")
            self.assertEqual(district["name"], "Melon Works")
            a, b = (pp.zone_rect(z) for z in plots)
            self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3], "never one over the other")
            gap = max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3])
            self.assertLessEqual(gap, 8, "side by side")
            # Its land as large as its blocks: more than one region cell, when they need it.
            self.assertGreaterEqual(district.get("spread", [1, 1])[0] * district.get("spread", [1, 1])[1], 2)
            whole = pp.bounds(district)
            one = pp.bounds({**district, "spread": [1, 1]})
            self.assertGreater((whole[2] - whole[0]) * (whole[3] - whole[1]), (one[2] - one[0]) * (one[3] - one[1]))
            self.assertIn("region.spread || [1, 1]", (WEB / "play-regions.js").read_text(encoding="utf-8"), "the page knows it")

    def test_a_projects_days_are_all_its_run_tasks_not_its_last_states(self) -> None:
        # 2026-11-18: a project's row lists only its last 600 states; its run tasks are kept whole.
        from datetime import datetime, timezone
        local = lambda y, m, d, h: datetime(y, m, d, h, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        rows = [{"minutes": 60, "starts_at": "2026-09-02T10:00:00+00:00", "ends_at": "2026-09-02T11:00:00+00:00"},
                {"minutes": 120, "starts_at": "2026-10-05T10:00:00+00:00", "ends_at": "2026-10-05T12:00:00+00:00"}]
        db = SimpleNamespace(query=lambda sql, args=(): rows if "state_run_tasks" in sql and args == ("p1",) else [])
        days = pw._project_day_minutes(db, "p1")
        self.assertEqual(sum(days.values()), 180)
        self.assertEqual(sorted(days), sorted({local(2026, 9, 2, 10).strftime("%Y-%m-%d"), local(2026, 10, 5, 10).strftime("%Y-%m-%d")}))
        self.assertEqual(pw._project_day_minutes(db, "t-not-a-project"), {})

    def test_the_world_follows_its_tasks_joined_into_its_ground_split_beside_it(self) -> None:
        # 2026-11-18: "如果能merge到原来的task里，就在原基础上扩建 ... 如果要split了或者create新的task, 再创建新的区域".
        from personalized_agent.agent import playregions as pr
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder)
            world = keeper.world()
            kiln = pr.open_region(world, dict(pr.STARTER), "Kiln", "w", "2026-11-18", None, pw.CELL_W, pw.CELL_H)
            tide = pr.open_region(world, dict(pr.STARTER), "Tide", "w", "2026-11-18", None, pw.CELL_W, pw.CELL_H)
            a = keeper._add_zone(world, "studio", "Pottery", "w", "2026-11-18", region=kiln)
            b = keeper._add_zone(world, "studio", "Pottery again", "w", "2026-11-18", region=tide)
            a["task"], b["task"] = "t-pots", "t-pots-twice"
            gone = {"id": "z90", "kind": "estate", "plot": True, "task": "t-pots-twice", "region": tide["id"],
                    "x": b["x"], "y": b["y"], "w": 20, "h": 16, "name": "", "why": "", "made": "", "grown": [], "door": 0}
            world["zones"].append(gone)
            world["zones"].append({**gone, "id": "z91", "task": "t-pots", "region": kiln["id"], "x": a["x"], "y": a["y"],
                                   "w": 30, "h": 20})                 # its own, larger: the one kept
            keeper._add_item(world, gone, {"kind": "plant", "why": "w"}, "2026-11-18")
            world["tasks"] = {"t-pots": {"minutes": 300, "day_minutes": {"2026-09-01": 300}},
                              "t-pots-twice": {"minutes": 100, "day_minutes": {"2026-09-01": 40, "2026-09-02": 60}}}
            lineage = {"t-pots-twice": {"kind": "new", "joined_into": "t-pots"},
                       "t-glaze": {"kind": "split from", "from": ["t-pots-twice"]},
                       "t-wrong": {"kind": "new", "joined_into": "t-undone"}, "t-undone": {"kind": "merged from", "retired": True}}
            c = keeper._add_zone(world, "studio", "Elsewhere", "w", "2026-11-18", region=tide)
            c["task"] = "t-wrong"
            keeper._follow(world, lineage)
            self.assertEqual((b["task"], b["was_tasks"]), ("t-pots", ["t-pots-twice"]), "its places the one it joined")
            self.assertNotIn(gone["id"], {z["id"] for z in world["zones"]}, "its ground let go")
            self.assertFalse([i for i in world["items"] if i["zone"] == gone["id"]])
            self.assertEqual(world["tasks"]["t-pots"]["day_minutes"], {"2026-09-01": 340.0, "2026-09-02": 60.0})
            self.assertEqual(world["tasks"]["t-pots"]["minutes"], 400)
            self.assertEqual(c["task"], "t-wrong", "a join into a task since retired is not followed")
            self.assertEqual(world["tasks"]["t-glaze"]["split_from"], "t-pots", "split from what it is now")
            # Split out: laid beside its parent's land, though they share no day.
            glaze = pr.open_region(world, dict(pr.STARTER), "Glaze", "w", "2026-11-18", None, pw.CELL_W, pw.CELL_H)
            keeper._add_zone(world, "studio", "Glazes", "w", "2026-11-18", region=glaze)["task"] = "t-glaze"
            world["tasks"]["t-glaze"].update(minutes=200, day_minutes={"2026-10-20": 200})
            keeper._lay_regions(world)
            home = next(r for r in world["regions"] if r["id"] == a["region"])
            self.assertLessEqual(max(abs(glaze["cell"][0] - home["cell"][0]), abs(glaze["cell"][1] - home["cell"][1])), 1)
            self.assertEqual(pr.area_of(glaze), pr.area_of(home), "not an area apart: it came out of it")

    def test_a_task_is_in_one_project_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = self._keeper(folder)
            world = keeper.world()
            first = keeper._keep_project(world, ["t1"], "Alone", "")
            keeper._keep_project(world, ["t2"], "Other", "")
            again = keeper._keep_project(world, ["t1", "t2"], "", "Joined.")
            self.assertIs(again, first)
            self.assertEqual(world["projects"], [{**first, "tasks": ["t1", "t2"], "name": "Alone", "summary": "Joined."}])


if __name__ == "__main__":
    unittest.main()

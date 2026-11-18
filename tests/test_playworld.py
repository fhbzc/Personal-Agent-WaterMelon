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
        self.assertEqual(keeper.grow("2026-11-17T21:00:00+00:00"), {"zones": 0, "items": 1})
        frame = next(one for one in keeper.load()["items"] if one["kind"] == "frame")
        self.assertEqual(frame["picture"], f"{frame['id']}.jpg")
        with Image.open(self.folder / "pictures" / frame["picture"]) as small:
            self.assertLessEqual(max(small.size), pw.PICTURE_SIDE)
        self.assertTrue(keeper.picture(frame["id"]))
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
            self.assertEqual(asked, [("2026-10-08", "2026-10-22"), ("2026-10-22", "2026-11-05"), ("2026-11-05", "2026-11-17")])
            world = keeper.load()
            self.assertEqual(world["caught_up"][:10], "2026-11-17")
            self.assertFalse(keeper.behind(world, now.isoformat()))
            plant = world["items"][-1]
            self.assertEqual(plant["days"], ["2026-11-05", "2026-11-17"], "the days it comes from, kept with it")
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
            self.assertEqual(tasks, [pw.TASK_LOOK, pw.TASK_THEMES, pw.TASK])
            self.assertIn("looked_up", keeper.models.asked[-1].messages[0]["content"])
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
            self.assertIn("deep greens", keeper.models.asked[-1].messages[0]["content"])
            self.assertIn("THEIR TASTES", keeper.models.asked[-1].system)


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
            self.assertTrue(frame["picture"], "the task's picture framed")
            self.assertIn("important_tasks", keeper.models.asked[-1].messages[0]["content"])
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
            self.assertTrue(names["Logo statue"]["picture"], "its logo made a statue")
            self.assertEqual(names["Robot"]["pixels"]["rows"][1], ".011110.")
            self.assertNotIn("Nothing", names, "too little drawn: not made")
            self.assertNotIn("No picture", names, "a banner holds a picture, or is not made")


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
            self.assertIn("neighbourhoods", keeper.models.asked[-1].messages[0]["content"])
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
        for kind, said in pw.ITEM_KINDS.items():
            # A thing of the catalogue is drawn as the scenery of its name (play.js playThing).
            self.assertIn(f"{kind}:", regions if said.get("thing") else source, f"every kind the model may choose is drawn: {kind}")


if __name__ == "__main__":
    unittest.main()

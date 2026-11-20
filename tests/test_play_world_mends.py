"""The Play world mended (the user, 2026-11-20): things never share an id; projects read however the answer wrote
them; a room grown elsewhere, even a land of its own, when it cannot grow where it stands; a great ground never over
another's places; a land ridden across; music described as it sounds; private screens never hung; pictures found in
their own files and from more than one place; the feeling of their field; themes counted; the way through in time;
the side panel over the whole screen. Everything here is made up."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

from personalized_agent.agent import playfind
from personalized_agent.agent import playmuse as muse
from personalized_agent.agent import playplaces as places
from personalized_agent.agent import playworld as pw
from personalized_agent.models.answers import json_objects

CONFIG = SimpleNamespace(get=lambda key, default=None: default)
WEB = support.ROOT / "personalized_agent" / "api" / "web"


class ByTask:
    """A model answering each call by its name, keeping what it was asked."""

    def __init__(self, said):
        self.said, self.asked = said, []

    def invoke(self, request, role=""):
        self.asked.append(request)
        return SimpleNamespace(text=json.dumps(self.said.get(request.task, {})))


def keeper_in(folder, said=None, **more):
    return pw.PlayWorld(Path(folder), ByTask(said or {}), CONFIG, **more)


class ThingsNeverShareAnIdTests(unittest.TestCase):
    def test_an_id_let_go_is_never_given_again(self) -> None:
        world = {"items": [{"id": "i1"}, {"id": "i2"}, {"id": "i3"}]}
        self.assertEqual(pw.new_item_id(world), "i4")
        world["items"] = [{"id": "i1"}]                 # two let go: "how many plus one" would be i2 again
        self.assertEqual(pw.new_item_id(world), "i5")
        self.assertEqual(world["item_top"], 5)

    def test_two_things_under_one_id_are_parted_when_the_world_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder)
            world = keeper.world()
            zone = world["zones"][0]["id"]
            world["items"] += [
                {"id": "i9", "zone": zone, "kind": "frame", "x": 1, "y": 1, "look_for": "a cover", "find_tries": 1},
                {"id": "i9", "zone": zone, "kind": "flowerbed", "x": 2, "y": 1, "find_tries": 10}]
            keeper.save(world)
            items = keeper.world()["items"]
            ids = [one["id"] for one in items]
            self.assertEqual(len(ids), len(set(ids)), "no two alike")
            bed = next(one for one in items if one["kind"] == "flowerbed")
            self.assertNotIn("find_tries", bed, "its count was the other's")


class ProjectsReadHoweverWrittenTests(unittest.TestCase):
    def test_every_whole_object_is_read_and_what_is_between_passed_over(self) -> None:
        text = '{"projects": [{"name": "A", "tasks": ["t1"]}]} and then {"projects": [{"name": "B", "tasks": ["t2"]}]}'
        self.assertEqual(len(json_objects(text)), 2)
        joined = pw.PlayWorld._conceived(text)
        self.assertEqual([p["name"] for p in joined["projects"]], ["A", "B"])
        alone = pw.PlayWorld._conceived('{"name": "C", "tasks": ["t3"], "blocks": []} (a note after it)')
        self.assertEqual(alone["projects"][0]["name"], "C", "a project written by itself")
        with self.assertRaises(ValueError):
            pw.PlayWorld._conceived("no JSON here")

    def test_each_answer_is_kept_with_its_call(self) -> None:
        source = (support.ROOT / "personalized_agent" / "models" / "call_history.py").read_text(encoding="utf-8")
        self.assertIn('getattr(response, "text", "")', source)


class ARoomGrowsElsewhereTests(unittest.TestCase):
    def test_too_large_for_its_land_it_moves_to_a_land_of_its_own_in_the_same_theme(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder)
            world = keeper.world()
            land = world["regions"][0]
            land.update(scene="autumn_valley", weather="changing", look="low stone walls", closed=False)
            zone = keeper._add_zone(world, "lab", "Lab", "Work.", "2026-10-01")
            zone.update(task="t1")
            with mock.patch.object(pw.PlayWorld, "_resize", return_value=False):   # no free ground here
                self.assertTrue(keeper._fit_to_cards(world, zone, 40))
            new = next(r for r in world["regions"] if r["id"] == zone["region"])
            self.assertNotEqual(new["id"], land["id"])
            self.assertEqual((new["scene"], new["weather"], new["look"]), ("autumn_valley", "changing", "low stone walls"))
            want = keeper.ROOM_BASE_TILES + keeper.TILES_A_CARD * 40
            self.assertGreaterEqual(len(keeper._floor_tiles(zone)), want, "as large as its work - never part way")
            self.assertEqual(zone["grown"][-1]["how"], "moved to a land of its own")


class AGreatGroundNeverOverOthersTests(unittest.TestCase):
    def test_its_places_far_apart_it_is_laid_on_free_ground(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder)
            world = keeper.world()
            region = world["regions"][0]
            a = keeper._add_zone(world, "garden", "A", "w", "2026-10-01")
            b = keeper._add_zone(world, "lab", "B", "w", "2026-10-01")
            other = keeper._add_zone(world, "cafe", "Theirs", "w", "2026-10-01")
            for zone, task in ((a, "t1"), (b, "t2"), (other, "t9")):
                zone["task"] = task
            project = {"id": "p1", "name": "Far Apart", "tasks": ["t1", "t2"]}
            own = {a["id"], b["id"]}
            got = keeper._great_free(world, project, b, [a, b], region, own)
            self.assertIsNotNone(got)
            land, (x0, y0, x1, y1) = got
            for zone in world["zones"]:
                if zone["id"] in own or zone.get("region") != land["id"]:
                    continue
                r = places.zone_rect(zone)
                self.assertFalse(r[0] < x1 and x0 < r[2] and r[1] < y1 and y0 < r[3], zone["name"])

    def test_a_land_named_for_a_project_is_never_named_to_the_web(self) -> None:
        source = (support.ROOT / "personalized_agent" / "agent" / "playworld.py").read_text(encoding="utf-8")
        self.assertIn('if region.get("district_of") else', source)


class ALandRiddenAcrossTests(unittest.TestCase):
    def test_places_far_apart_in_one_land_are_joined_by_a_line(self) -> None:
        land = {"id": "r1", "biome": "meadow"}
        zones = [{"id": "z1", "x": 0, "y": 0, "w": 10, "h": 6, "region": "r1"},
                 {"id": "z2", "x": 80, "y": 40, "w": 10, "h": 6, "region": "r1"}]
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder)
            lines = keeper._local_lines({"zones": zones, "projects": []}, [land])
        self.assertTrue(lines and lines[0]["whole_land"])
        self.assertEqual(lines[0]["places"], ["z1", "z2"])


class MusicAsItSoundsTests(unittest.TestCase):
    def test_every_track_measured_and_the_tight_loop_never_calm(self) -> None:
        self.assertEqual(set(pw.TRACK_MEASURES), set(pw.TRACKS))
        for name, (notes, loud) in pw.TRACK_MEASURES.items():
            pace = re.search(r" - (?:very )?(slow|steady|busy)\b", pw.TRACKS[name]).group(1)
            self.assertEqual(pace, "busy" if notes > 3 else "steady" if notes >= 2 else "slow", name)
        self.assertIn("never a calm place", pw.TRACKS["just-saying-tho"])
        self.assertIn("NOT calm", pw.TRACKS["calm-loop"])
        sound = (WEB / "play-sound.js").read_text(encoding="utf-8")
        cafe = re.search(r"\n  cafe: \{ tempo: (\d+)", sound)
        self.assertLess(int(cafe.group(1)), 80, "a tea house's own tune is slow")


class PrivateScreensNeverHungTests(unittest.TestCase):
    def test_mail_social_job_and_accounts_but_not_their_own_work(self) -> None:
        for said in ("Gmail Inbox (29,100)", "the LinkedIn feed", "a Greenhouse job application", "微信聊天窗口",
                     "online banking"):
            self.assertTrue(pw.private_screen({"what": said}), said)
        for said in ("The sidebar chat of the agent", "a news feed of releases", "A CV paper on detection",
                     "a terminal running tests"):
            self.assertFalse(pw.private_screen({"what": said}), said)

    def test_one_hung_before_is_taken_down(self) -> None:
        world = {"items": [{"id": "i1", "shot": "shot-i1.jpg", "shot_what": "Gmail inbox open"},
                           {"id": "i2", "shot": "shot-i2.jpg", "shot_what": "their figure"}]}
        self.assertEqual(pw.PlayWorld._private_taken_down(world), 1)
        self.assertNotIn("shot", world["items"][0])
        self.assertIn("shot", world["items"][1])


class PicturesFromTheirFilesTests(unittest.TestCase):
    def test_a_folder_is_drawn_as_its_tree_and_a_text_as_its_lines(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "replication"
            (root / "code").mkdir(parents=True)
            (root / "__pycache__").mkdir()
            (root / "code" / "run.py").write_text("print('made up')\n", encoding="utf-8")
            (root / "README.md").write_text("# A made-up package\n\nData and code.\n", encoding="utf-8")
            tree = playfind._tree_of(root)
            self.assertIn("code/", tree)
            self.assertFalse(any("__pycache__" in line for line in tree))
            self.assertTrue(playfind.from_file(root, Path(folder) / "tree.png"))
            self.assertTrue(playfind.from_file(root / "README.md", Path(folder) / "text.png"))

    def test_their_files_are_offered_by_name_from_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder, files=lambda words: [{"path": str(Path(folder) / "pkg" / "README.md")}])
            found = keeper._their_files("replication package")
        self.assertEqual([one["kind"] for one in found], ["file", "folder"])
        self.assertTrue(all(one["id"].startswith("file-") for one in found))


class TheFeelingOfTheirFieldTests(unittest.TestCase):
    def test_the_field_is_searched_their_words_known_to_the_guard(self) -> None:
        said = {muse.TASK_MOOD: {"mood": "Steady days; their field in a storm.",
                                 "fields": [{"field": "AI", "events": "A big release.", "feeling": "turmoil"}]},
                muse.TASK_MOOD_SCENE: {"theme": "water", "scene": "misty_lake", "land_name": "Still Water", "why": "w"}}
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder, said, feelings=lambda s, u: {"their_words": ["one more bug"], "their_rhythm": ["d"]})
            keeper._important_info = {"t1": {"title": "A made-up model"}}
            mood = keeper._mood(keeper.world(), "2026-11-01", "2026-11-08")
            first, second = keeper.models.asked
        self.assertTrue(first.builtin_web, "the field's events searched")
        self.assertIsNotNone(first.web_guard, "their words known to the search's guard")
        # 2026-11-20: "搜索检查只查密码邮箱之类，项目名人名都不拦" - only what is secret stopped.
        self.assertIsNone(first.web_guard.check_query("A made-up model news"), "names let through")
        self.assertIsNotNone(first.web_guard.check_query("mail someone@example.org"), "a mail address stopped")
        self.assertIn("their_work", first.messages[0]["content"])
        self.assertEqual(mood["fields"][0]["field"], "AI")
        self.assertIn("FIELD", second.system)

    def test_each_theme_and_scene_says_how_often_it_was_a_land(self) -> None:
        text = muse.mood_scene_system(open_now=muse.scenes_open([]), used=["autumn_valley", "autumn_valley"])
        self.assertIn("autumn_valley [used 2 times]", text)
        self.assertIn("never used", text)

    def test_the_themes_call_is_told_how_often_each_was_chosen(self) -> None:
        self.assertIn("themes_used_before", pw.THEMES_SYSTEM)


class TheWayThroughInTimeTests(unittest.TestCase):
    def test_a_rooms_things_in_the_order_their_cards_were_first_worked_on(self) -> None:
        world = {"work_trees": {"t1": [{"id": "c1", "first": "2026-10-05"}, {"id": "c2", "first": "2026-10-01"},
                                       {"id": "c3"}]},
                 "items": [{"id": "i1", "zone": "z1", "card": "c1"}, {"id": "i2", "zone": "z1", "card": "c2"},
                           {"id": "i3", "zone": "z1", "card": "c3"}, {"id": "i4", "zone": "z1", "card": "c2"}],
                 "zones": [{"id": "g", "great": "p1"}, {"id": "a", "in_great": "p1", "days": ["2026-10-09"]},
                           {"id": "b", "in_great": "p1", "days": ["2026-10-02"]}]}
        routes = pw.PlayWorld.routes(world)
        self.assertEqual(routes["rooms"]["z1"], ["i2", "i1", "i3"], "by when first worked on; a card once")
        self.assertEqual(routes["grounds"]["g"], ["b", "a"], "a project's parts as they began")
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn('layer("routes", () => playRoutesDraw(size));', play)
        self.assertIn("ITS WAY THROUGH, AS IT WAS LIVED", muse.ROOM_SYSTEM)


class TheSidePanelTests(unittest.TestCase):
    def test_over_the_whole_screen_and_back(self) -> None:
        notes = (WEB / "notes.js").read_text(encoding="utf-8")
        self.assertIn('id="side-full"', notes)
        self.assertIn("/api/notifications/panel_full", notes)
        routes = (support.ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('@route("POST", "/api/notifications/panel_full")', routes)
        desktop = (support.ROOT / "personalized_agent" / "ui" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn("never docked meanwhile", desktop)
        self.assertIn("no width of it kept", desktop)

    def test_its_edge_is_taken_where_it_is_seen_whatever_the_zoom(self) -> None:
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".side-grip { position: fixed; left: 0; top: 0; bottom: 0; width: calc(12px / var(--side-zoom, 1));",
                      css)


class ANewFeelingOpensANewLandTests(unittest.TestCase):
    def test_the_first_lands_age_is_in_the_worlds_days(self) -> None:
        """2026-11-20: "你现在怎么样会换场景主题，我看我上个生成的世界好像一直没换" - the first land kept the clock's day it
        was made, so a September stretch was always before it and no new land ever opened."""
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder)
            world = keeper.world()
            land = world["regions"][0]
            land.update(scene="autumn_valley", made="2026-11-19T21:13:00+00:00", starter=False)
            land.pop("closed", None)
            world["moods"] = [{"since": "2026-08-31", "until": "2026-09-14", "scene": "autumn_valley"}]
            mood = {"since": "2026-09-14", "until": "2026-09-28", "scene": "golden_farmland", "changed": True}
            self.assertTrue(keeper._mood_opens_land(world, mood, "2026-09-28"))
            world["regions"].append({"id": "r9", "scene": "autumn_valley", "district_of": "t1",
                                     "made": "2026-11-20T01:00:00+00:00"})
            self.assertTrue(keeper._mood_opens_land(world, mood, "2026-09-28"), "a project's own land is not the feeling's")

    def test_the_first_land_opened_keeps_its_days(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder)
            world = keeper.world()
            keeper._open_outside(world, {"scene": "autumn_valley", "land_name": "A valley", "why": "w",
                                         "since": "2026-08-31", "until": "2026-09-14"}, "2026-11-19T21:13:00+00:00")
            self.assertEqual(world["regions"][0]["days"], ["2026-08-31", "2026-09-14"])


class LandsChangeMoreOftenTests(unittest.TestCase):
    def test_three_days_and_a_small_swing_counts(self) -> None:
        """2026-11-20: "这个改成3天就行"; "心情小起伏也行，变的更频繁一点挺好的"; then "每隔1天就允许变化（而不是3天）"."""
        self.assertEqual(muse.MOOD_LAND_DAYS, 1)
        self.assertIn("A SMALL SWING COUNTS TOO", muse.MOOD_SCENE_SYSTEM)



class TheLibraryOfPiecesTests(unittest.TestCase):
    """2026-11-20: "画过的大型控件也添加描述，作为一个之后可以复用的东西"; "所有的大型，小型控件，都应该可以build on top of"."""

    def test_a_piece_is_kept_and_used_again_without_a_call(self) -> None:
        from personalized_agent.agent import playpieces as pieces
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder)
            world = keeper.world()
            zone = keeper._add_zone(world, "garden", "A garden", "w", "2026-10-01")
            keeper.save(world)
            spec = {"outline": "ellipse", "bridges": 1, "boats": 3, "boat": "swan", "name": "Swan Lake", "why": "w"}
            keeper._keep_piece({"kit": "water", "w": 4, "h": 3, "what": "A lake with swan boats"}, spec, "2026-10-02")
            shelf = keeper._made_library()["pieces"]
            self.assertIn("swan_lake", shelf)
            self.assertIn("swan boats", pieces.pieces_told(keeper._made_library()))
            fx, fy = zone["x"], zone["y"] + 1
            made = keeper._build_pieces(zone["id"], [{"kit": "water", "what": "a lake", "x": fx, "y": fy, "w": 5, "h": 4,
                                                      "use": "swan_lake"}], "2026-10-03")
            self.assertEqual(made, 1)
            self.assertEqual(keeper.models.asked, [], "used again as it was: no call")
            piece = next(i for i in keeper.world()["items"] if i.get("kind") == "piece")
            self.assertEqual((piece["w"], piece["h"], piece["used"], piece["interact"]), (4, 3, "swan_lake", "ride"))

    def test_asked_pieces_and_things_keep_what_they_build_on(self) -> None:
        from personalized_agent.agent import playmade
        from personalized_agent.agent import playpieces as pieces
        asked = pieces.read_asked({"pieces": [{"kit": "stack", "what": "a taller tower", "x": 0, "y": 0, "w": 3, "h": 3,
                                               "base": "clock_tower", "change": "a storey more"}]},
                                  ["......"] * 6, 0, 0, set(), 2)
        self.assertEqual((asked[0]["base"], asked[0]["change"]), ("clock_tower", "a storey more"))
        library = {"items": {"red_lamp": {"description": "a lamp", "size": [1, 1], "svg": "<svg/>"}}}
        made = playmade.read_asked({"make": [{"kind": "thing", "name": "blue lamp", "description": "a blue one",
                                              "w": 1, "h": 1, "base": "red_lamp", "change": "blue"}]}, library,
                                   lambda name: False)
        self.assertEqual(made[0]["base"], "red_lamp")
        self.assertEqual(playmade.bases_of(made, library)["red_lamp"]["svg"], "<svg/>")
        self.assertIn("build on", pieces.room_pieces_note({}).lower())

    def test_boats_are_read_and_drawn_round_their_water(self) -> None:
        from personalized_agent.agent import playpieces as pieces
        self.assertEqual(pieces.boats_of({"boats": 9, "boat": "swan"}, 0), {"boats": pieces.BOATS_MOST, "boat": "swan"})
        self.assertEqual(pieces.boats_of({"boats": 2, "boat": "submarine"}, 0)["boat"], "rowboat")
        self.assertEqual(pieces.boats_of({}, 0), {})
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("function playBoatsDraw(item, size, now)", play)
        self.assertIn("play.ride = { piece: item, at: performance.now(), boat: true };", play)


class StationsAndPlacesTests(unittest.TestCase):
    def test_a_place_id_let_go_is_never_given_again(self) -> None:
        world = {"zones": [{"id": "z1"}, {"id": "z2"}, {"id": "z3"}]}
        self.assertEqual(pw.new_zone_id(world), "z4")
        world["zones"] = [{"id": "z1"}]
        self.assertEqual(pw.new_zone_id(world), "z5")

    def test_a_station_ground_is_the_codes_and_its_line_stops_there(self) -> None:
        self.assertIn("terminal", pw.ZONE_KINDS)
        self.assertIn("terminal", pw.CODE_KINDS)
        self.assertIn("terminal", places.OUTDOOR)
        act = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("one.zone.terminal_of === `${land.region.id}:${runs}`", act)
        self.assertIn("function playRideBetween(fromRegion, toRegion)", act)
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn('playWords("Take the {0}, get off at {1}")', play)
        zh = json.loads((WEB / "lang" / "zh.json").read_text(encoding="utf-8"))
        self.assertEqual(zh["Take the {0}, get off at {1}"], "乘{0}，在{1}下车")
        self.assertIn("air", pw.PlayWorld.STATION_OF)

    def test_outside_the_road_of_their_life_has_arrows_and_denser_signs(self) -> None:
        regions_js = (WEB / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("const PLAY_POST_EVERY = 10, PLAY_POST_APART = 5;", regions_js)
        self.assertIn("return { order, follows, posts, routes };", regions_js)
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("for (const path of (map.life && map.life.routes) || [])", play)



class RealmsAndCitiesTests(unittest.TestCase):
    """2026-11-20: "要加水下/海底类的主题场景，和天空/云端的主题场景，还有太空/宇宙的主题场景，加上专属音乐，房子，区块和特色装饰，
    交通工具"; "还要加城市主题，里面有常见建筑"; "所有交通工具设置的时候要考虑场景适配性"; "不同主题之间的交通连接和衔接隔断也要model
    根据场景适配性判断"."""

    def test_each_realm_a_theme_with_its_own(self) -> None:
        sound = (WEB / "play-sound.js").read_text(encoding="utf-8")
        regions_js = (WEB / "play-regions.js").read_text(encoding="utf-8")
        for theme in ("undersea", "sky", "cosmos", "city"):
            for scene in muse.SCENE_THEMES[theme][1]:
                self.assertIn(scene, muse.SCENE_OWN, scene)
                self.assertIn(f"\n  {scene}: {{ tempo:", sound, scene)
        for edge in ("coralreef", "kelp", "cloudbank", "starfield"):
            self.assertIn(edge, muse.EDGE_KINDS)
            self.assertIn(f"  {edge}: {{ ground:", regions_js)
        materials = json.loads((WEB / "play" / "materials.json").read_text(encoding="utf-8"))
        for floor in ("coral_sand", "pearl_shell", "moon_dust", "star_glass"):
            self.assertIn(floor, materials["floors"])
        for wall in ("cloud_puff", "hull_plate", "shell"):
            self.assertIn(wall, materials["walls"])

    def test_a_land_of_a_realm_keeps_its_own_in_its_look(self) -> None:
        region = {"look": "Low domes."}
        pw.PlayWorld._land_own(region, "moon_base")
        self.assertIn("solar panels", region["own"])
        self.assertIn("Its own:", region["look"])

    def test_every_vehicle_to_its_scene_and_each_realm_reached_by_its_own(self) -> None:
        lands = [{"id": "r1", "biome": "meadow"}, {"id": "r2", "biome": "skycity"}, {"id": "r3", "biome": "underwater"},
                 {"id": "r4", "biome": "space"}]
        lines = muse.read_transport({"lines": [{"vehicle": "train", "stops": ["r1", "r2"]}]}, lands, [], None)["lines"]
        self.assertFalse([one for one in lines if one["vehicle"] == "train" and "r2" in one["stops"]], "no train in the sky")
        by = {one["vehicle"]: one["stops"] for one in lines}
        self.assertEqual((by.get("airship"), by.get("submarine"), by.get("spaceship")), (["r2", "r1"], ["r3", "r1"], ["r4", "r1"]))
        self.assertEqual(muse.local_lines([{"id": "a", "x": 0, "y": 0, "w": 4, "h": 4}, {"id": "b", "x": 90, "y": 0, "w": 4, "h": 4}],
                                          {"id": "r3", "biome": "underwater"})[0]["vehicle"], "submarine")
        act = (WEB / "play-act.js").read_text(encoding="utf-8")
        for look in ("submarine", "balloon", "rocket", "ufo"):
            self.assertIn(f"  {look}: {{ kind:", act)

    def test_how_two_lands_meet_is_the_models_judgement(self) -> None:
        lands = [{"id": "r1", "biome": "meadow"}, {"id": "r2", "biome": "skycity"}]
        joins = muse.read_transport({"joins": [{"from": "r1", "to": "r2", "how": "clouds", "why": "w"},
                                               {"from": "r1", "to": "r2", "how": "void"},
                                               {"from": "r1", "to": "r9", "how": "glass"},
                                               {"from": "r2", "to": "r1", "how": "lava"}]}, lands, [], None)["joins"]
        self.assertEqual([(j["from"], j["to"], j["how"]) for j in joins], [("r1", "r2", "clouds")])
        self.assertIn("HOW EACH TWO LANDS MEET", muse.transport_system())
        regions_js = (WEB / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("for (const one of (world.transport && world.transport.joins) || [])", regions_js)

    def test_a_city_has_its_own_buildings_on_ground_only_the_code_makes(self) -> None:
        self.assertEqual(set(pw.PlayWorld.CIVIC), {"mall", "apartments", "skyscraper", "hospital", "police", "fire"})
        self.assertIn("civic", pw.CODE_KINDS)
        self.assertIn("civic", places.OUTDOOR)



class TheLandMovesTests(unittest.TestCase):
    def test_water_clouds_stars_and_drift_in_sight_only_slowly_only_with_play_open(self) -> None:
        """2026-11-20: "水...能不能用动画做出流动感"; "云也会飘动（争取每个场景都有一些地形运动的元素）"; "低帧率就行";
        "所有动画都是只在play打开"; "只有视野内的才画流动"; "内陆湖的河流颜色和海水可以不一样"."""
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn('layer("motion", () => playMotionDraw(size, c0, c1, r0, r1));', play)
        self.assertIn("const PLAY_MOTION_FPS = 6;", play)
        self.assertIn("if (!play.on) { play.motionTimer = null; return; }", play)
        self.assertIn('if (piece.startsWith("waterFresh")) {', play)
        regions_js = (WEB / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn('waterFresh: ["rpg", 0, 0], waterFresh2: ["rpg", 1, 0],', regions_js)
        self.assertIn("!sea[i] && !shore[i] && !rim[i]", regions_js, "a river on its land only")


if __name__ == "__main__":
    unittest.main()

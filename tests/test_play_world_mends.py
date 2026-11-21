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
        self.assertIn(".side-grip { position: fixed; left: 0; top: 0; bottom: 0; width: calc(3px / var(--side-zoom, 1));",
                      css)
        # 2026-11-20: "放在白色线的中间，就应该变成缩放的形状" - as far again outside the line, a strip of the desktop's own
        desktop = (support.ROOT / "personalized_agent" / "ui" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn("SIDE_EDGE_OUTSIDE = 3", desktop)
        self.assertIn("app_window.edge_strip_titled(self._notes_titles(), handle, self.SIDE_EDGE_OUTSIDE)", desktop)
        self.assertIn('strip.bind("<ButtonPress-1>", lambda _event: DesktopAgent._side_edge_pressed(self))', desktop)


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

    def test_a_bus_keeps_to_the_roads(self) -> None:
        # The user, 2026-11-20: buses ran the way a plane flies - every bus on the roads alone, or none.
        act = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("function playRoadRoute(paths, a, b)", act)
        self.assertIn("function playRoadNear(paths, hx, hy)", act)
        # 2026-11-20: "我说的路不是能走的地面，而是真的道路" - streets: wide and paved.
        self.assertIn("route = playRoadRoute(streets, a, b);", act)
        self.assertIn("onRoad ? (x, y) => streets.has(`${x},${y}`)", act)
        self.assertIn('const PLAY_PAVED = new Set(["asphalt", "paving", "pavement2", "cobble", "cobble2", "stone"]);', act)
        self.assertNotIn("if (!one.wide) continue;", act, "a tile wide is a street too, paved")
        self.assertIn("if (!leg && (afloat || onRoad)) { line.none = true; break; }", act)

    def test_each_road_its_edge_and_the_play_button_its_switch(self) -> None:
        # 2026-11-20: "还是一团" - a road told from paved ground; "play 的按钮要根据是否on /off 变色".
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn('if (apart(wx, wy - 1)) context.fillRect(tx * 16, ty * 16, 16, 2);', play)
        self.assertIn('return !there || (there.material || "") !== (here.material || "");', play, "two roads apart too")
        self.assertIn('button?.setAttribute("data-power", playPowered() ? "on" : "off");', play)
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.assertIn('#side-play-open[data-power="on"] { color: #3fb950; }', css)
        self.assertIn('#side-play-open[data-power="off"]', css)

    def test_no_fringe_on_a_road_dragging_lays_nothing_a_bus_turns_square(self) -> None:
        # 2026-11-20: a jagged strip beside a road; "view很大的时候 ... 拖动还是卡"; "bus ... 应该是拐直角弯的".
        regions = (WEB / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("      if (paths.has(`${x},${y}`)) continue;\n      for (let dy = 0; dy < w; dy += 1) {", regions)
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("const moving = playMoving() || (play.lastLook && performance.now() - play.lastLook < 400);", play)
        act = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("leg = onRoad ? playSquareLeg(leg) :", act)
        self.assertIn('if (ok && line.kind !== "road") pts.splice(0, pts.length, ...playRounded(pts, ok));', act)

    def test_roads_one_or_apart_never_glued(self) -> None:
        # 2026-11-20: "路都黏在一块了"; "你的路的seperation能不能clean, 你现在几条路放一起就和一条路一样".
        regions = (WEB / "play-regions.js").read_text(encoding="utf-8")
        self.assertIn("const PLAY_ROAD_BESIDE = 4;", regions)
        self.assertIn("(paths && paths.beside && paths.beside.has(key) ? PLAY_ROAD_BESIDE : 0)", regions)
        self.assertIn("paths.beside = paths.beside || new Set();", regions)

    def test_a_hall_on_a_great_ground_is_the_place_one_is_in(self) -> None:
        # 2026-11-20: "这个房间里的东西好像没法交互" - the ground, found first, was taken for where one stood.
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("if (tx >= fx && tx < fx + fw && ty >= fy && ty < fy + fh && fw * fh < zoneArea) { zone = one; zoneArea = fw * fh; }", play)
        self.assertNotIn("const zone = play.world.zones.find((one) => {", play)

    def test_the_view_follows_every_ride(self) -> None:
        # 2026-11-20: "有些交通工具，agent坐上去了之后，没有自动追踪agent的位置".
        play = (WEB / "play.js").read_text(encoding="utf-8")
        act = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("play.me.x = p.x; play.me.y = p.y;\n        if (typeof playActMeOn", play, "a drawn track's car carries the walker")
        self.assertEqual(play.count("play.lookAt = null;                        // the view on the walker again"), 2)
        self.assertIn("  play.lookAt = null;\n  if (play.pan) play.pan.clear();", act)

    def test_zoomed_out_the_land_holds_still(self) -> None:
        # 2026-11-20: "view很大的时候(比如1/2个world), 整个地图很卡" - the motion layer 43 of 55 ms a frame at a half.
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("if (scale >= 1) layer(\"motion\", () => playMotionDraw(size, c0, c1, r0, r1));", play)
        self.assertIn("else play.motionSeen = false;", play)

    def test_the_view_moves_faster_with_shift(self) -> None:
        # 2026-11-20: "wasd 移动view的时候按住shift也可以加速".
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("const speed = PLAY_PAN_SPEED * (play.running ? PLAY_PAN_FAST : 1)", play)
        self.assertIn("play.running = event.shiftKey;", play)

    def test_signs_lifted_once_for_the_world_not_each_frame(self) -> None:
        # 2026-11-20: "这里四个牌子我移动view的时候会不停闪"; "上下移动会闪，左右不会".
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("if (!play.signLifts || play.signLifts.key !== sizeKey || play.signLifts.signs !== signs) {", play)
        self.assertIn("const all = signs.map((sign) => ({ sign, x: Math.round(sign.at[0] * size), y: Math.round(sign.at[1] * size) }))", play)

    def test_play_switched_off_loads_nothing_and_is_dark(self) -> None:
        # 2026-11-20: "play要有一个开关 ... 关掉，这样play就不加载，不占资源，我切换play panel就是全黑的".
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn('if (play.off) { playNote("opened switched off: nothing loaded"); return; }', play)
        self.assertLess(play.index('if (play.off) { playNote("opened switched off'), play.index("await playLoadSheets();"),
                        "off: returns before anything is loaded")
        self.assertIn("function playUnload() {", play)
        self.assertIn("if (!play.on || play.off || !play.map || !play.ctx) return;", play)
        self.assertIn('data-play-power', play)
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        # 2026-11-20: "你这个按钮和原来关机的放在一个位置不就行了" - the one switch, in its place, on the dark.
        self.assertIn(".side-play.off .side-play-zoom > :not([data-play-power]) { display: none !important; }", css)
        self.assertNotIn("side-play-power-on", play)
        zh = json.loads((WEB / "lang" / "zh.json").read_text(encoding="utf-8"))
        self.assertEqual(zh["Turn Play on"], "打开 Play")

    def test_clouds_birds_and_motes_stay_where_they_are_in_the_world(self) -> None:
        # 2026-11-20: "你这些云是根据view算的（包括飞鸟也是）我移动view 云也会跟着移".
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("const ax = playHash(gx, gy, 500 + k) * P, ay = playHash(gx, gy, 600 + k) * P;", play)
        self.assertIn('if (biome !== "skycity") return;', play)
        self.assertNotIn("cx = x0 - 6 + ((k * 13.7", play, "no cloud placed by the view")
        act = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("const [fx, fy] = playActScreen((flock.x || 0) + gone, flock.y || 0);", act)
        self.assertNotIn("flock.y * canvas.height", act, "no bird placed by the screen")

    def test_arrows_dense_each_way_its_own_colour(self) -> None:
        # 2026-11-20: "道路地上的箭头可以密集一点，并且不同path用不同颜色，我刚刚说的不密只是说牌子不密".
        play = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("for (let k = 1; k < path.length - 1; k += 2) {", play)
        self.assertIn("for (let k = 2; k < path.length - 2; k += 3) {", play)
        self.assertIn("ctx.fillStyle = PLAY_ARROW_COLOURS[(one.c || 0) % PLAY_ARROW_COLOURS.length];", play)
        self.assertIn("if (taken.has(`${x},${y}`)) return;", play)

    def test_outside_the_road_of_their_life_has_arrows_and_sparser_signs(self) -> None:
        regions_js = (WEB / "play-regions.js").read_text(encoding="utf-8")
        # Sparser (the user, 2026-11-20): the post by the place a road leads to first, then one every 24 tiles.
        self.assertIn("const PLAY_POST_EVERY = 24, PLAY_POST_APART = 10;", regions_js)
        self.assertLess(regions_js.index("postBeside(route, route.length - 3, one, from);"),
                        regions_js.index("for (let k = 12; k < route.length - 12; k += PLAY_POST_EVERY)"))
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


class PictureAddressesTest(unittest.TestCase):
    """Play's picture reader (2026-11-20): an address in any language is sent encoded; sites that refuse every
    program are never asked; a site that said "too many" or refused is left alone a while; one site asked at most
    every SPACING seconds."""

    def setUp(self) -> None:
        playfind._held_until.clear()
        playfind._asked_at.clear()

    def test_an_address_in_another_language_is_sent_encoded(self) -> None:
        self.assertEqual(playfind._ascii_address("https://zh.example.org/wiki/File:西瓜.jpg?q=中&r=%E4%B8%AD"),
                         "https://zh.example.org/wiki/File:%E8%A5%BF%E7%93%9C.jpg?q=%E4%B8%AD&r=%E4%B8%AD")
        self.assertTrue(playfind._ascii_address("https://例子.example/路").startswith("https://xn--"))

    def test_refusing_sites_never_asked_and_a_429_holds_the_site(self) -> None:
        sent = []

        def refuse(request, timeout=0):
            sent.append(request.full_url)
            raise playfind.urllib.error.HTTPError(request.full_url, 429, "Too many", {"Retry-After": "120"}, None)
        with mock.patch.object(playfind.urllib.request, "urlopen", refuse), \
                mock.patch.object(playfind, "SPACING", 0.0):
            self.assertIsNone(playfind._read("https://www.linkedin.com/in/someone"))
            self.assertIsNone(playfind._read("https://media.linkedin.com/a.png"))
            self.assertEqual(sent, [], "never asked")
            self.assertIsNone(playfind._read("https://images.example.org/a.png"))
            self.assertIsNone(playfind._read("https://images.example.org/b.png"))
        self.assertEqual(len(sent), 1, "left alone after its 429")
        self.assertTrue(all(one.isascii() for one in sent))

    def test_one_site_is_asked_in_turn(self) -> None:
        waited = []
        with mock.patch.object(playfind.time, "sleep", waited.append):
            self.assertTrue(playfind._wait_turn("one.example.org"))
            self.assertTrue(playfind._wait_turn("one.example.org"))
            self.assertTrue(playfind._wait_turn("two.example.org"))
        self.assertEqual(len(waited), 1)
        self.assertGreater(waited[0], 0)


class PlayLoadsLessAndSoonerTests(unittest.TestCase):
    """The user, 2026-11-20: "4个都做" - kept by distance, drawn as it is shown, drawn once and kept, the keeping off
    the page's thread."""

    def setUp(self):
        self.page = (WEB / "play.js").read_text(encoding="utf-8")
        self.regions = (WEB / "play-regions.js").read_text(encoding="utf-8")

    def test_what_is_far_from_the_view_is_let_go_of_and_freed(self):
        self.assertIn("const PLAY_KEEP_RING = 2, PLAY_PIECE_UNSEEN_MS = 120000;", self.page)
        self.assertIn("playLetGoFar(c0, c1, r0, r1, half);", self.page)
        self.assertIn("if (made) made.seenAt = performance.now();", self.page, "a piece drawn is kept")
        self.assertIn("playFree(play.svgPieces.get(oldest));", self.page)
        self.assertEqual(self.page.count("play.chunks.clear();"), 1, "every clearing frees first - only in playChunksClear")
        self.assertIn("for (const one of play.chunks.values()) playFree(one);\n  play.chunks.clear();", self.page)

    def test_far_out_a_block_is_drawn_at_half_its_pixels(self):
        self.assertIn("canvas.width = canvas.height = PLAY_CHUNK * PLAY_TILE * (half ? 0.5 : 1);", self.page)
        self.assertIn("if (half) context.scale(0.5, 0.5);", self.page)
        self.assertIn("const far = scale < 0.5, half = scale < 1, map = play.miniBase;", self.page)

    def test_great_pieces_are_finer_than_tiles_but_cheaper_and_moving_parts_cropped(self):
        self.assertIn("const PLAY_FINE = 2, PLAY_FINE_COLOURS = 24;", self.page)
        self.assertIn("crop: true", self.page)
        self.assertIn("part.width * k, part.height * k", self.page)

    def test_a_block_drawn_is_kept_with_its_mark_and_read_back_off_the_thread(self):
        self.assertIn('const PLAY_CHUNK_KEPT_VERSION = 1, PLAY_CHUNK_DB = "play-blocks", PLAY_CHUNKS_STORED_MOST = 1500;', self.page)
        self.assertIn("read.result && read.result.mark === mark ? read.result.blob : null", self.page, "another mark: drawn anew")
        self.assertIn("createImageBitmap(blob)", self.page)
        self.assertIn("if (play.chunkIncomplete || (play.tilesLoading || 0) > 0) return;", self.page, "nothing half-drawn kept")
        self.assertIn("const state = playChunkFromStore(cx, cy, half);", self.page, "the ring round the view asks the store too")

    def test_a_block_drawn_another_way_is_drawn_anew_and_the_store_is_kept_small(self):
        # the drawing's own code in the mark: a change to it needs no number raised by hand
        self.assertIn("return `${PLAY_CHUNK_KEPT_VERSION}|${playChunkDrawing()}|${play.language", self.page)
        self.assertIn("for (const one of [playChunk, playChunkPaint, playTile, playWallTile, playLight, playThing, playFloorArt, playLayBlock,", self.page)
        self.assertIn("PLAY_CHUNKS_STORED_MOST = 1500", self.page)
        self.assertIn("setTimeout(playChunkStorePrune, 20000);", self.page)

    def test_the_keeping_is_a_workers_and_the_worker_is_ended_with_play(self):
        self.assertIn("const PLAY_KEEP_WORKER_SOURCE = `", self.page)
        self.assertIn("worker.postMessage({ key, mark, bitmap }, [bitmap])", self.page)
        self.assertIn("playKeepWorker.terminate();", self.page)
        self.assertNotIn("new Worker(\"", self.page, "started from its own text, no file of its own to serve")

    def test_zoomed_out_great_pieces_are_made_while_idle_and_never_while_the_view_moves(self):
        self.assertIn("if (play.scale < 1 && !playSvgPieceNow) {", self.page)
        self.assertIn("if (playMoving() || (play.lastLook && performance.now() - play.lastLook < 400)) {", self.page)
        self.assertIn("} while (waiting.size && deadline.timeRemaining() > 12);", self.page)

    def test_a_great_pieces_pictures_are_made_once_and_kept(self):
        # the user, 2026-11-20: "他们生成一次之后就保存成素材不就好了吗"
        self.assertIn("const storeKey = `piece|${item.id}|${shown}`, mark = playPieceMark(spec, shown);", self.page)
        self.assertIn("playPieceFromStore(storeKey, mark, made).then((found) => {", self.page)
        self.assertIn("worker.postMessage({ key: storeKey, mark, bitmaps, meta }, bitmaps);", self.page)
        self.assertIn("const PLAY_PIECES_KEPT = 400;", self.page, "far out every piece is in sight: 24 made them again and again")

    def test_far_out_nothing_holds_the_page_long_enough_to_break_the_music(self):
        # the user, 2026-11-20: "音乐在view最大的时候一卡一卡的"
        sky = (WEB / "play-sky.js").read_text(encoding="utf-8")
        self.assertIn("const block = laid.get(`${cx},${cy}`);", sky, "the night's lamps from blocks laid, none laid for them")
        self.assertNotIn("playBlock(play.map, cx, cy).deco", sky)
        self.assertIn("image.onload = () => playOneAtATime(() => done(image));", self.page)
        self.assertIn("const small = playChunk(cx, cy, false, true);", self.page, "the map's block drawn at its size, not read back")
        self.assertNotIn("small.toDataURL(", self.page)
        self.assertIn("const PLAY_MUSIC_AHEAD = 1.5;", (WEB / "play-sound.js").read_text(encoding="utf-8"))

    def test_laid_one_frame_and_drawn_the_next(self):
        self.assertIn("playBlock(play.map, waiting[toLay][0], waiting[toLay][1]);", self.page)

    def test_the_ways_found_are_kept_and_given_back_by_what_was_asked(self):
        self.assertIn("function playRouteFind(blocked, from, to, wobble, salt, paths) {", self.regions)
        self.assertIn("if (memo && memo.routes.has(key)) return memo.routes.get(key).map((one) => one.slice());", self.regions)
        self.assertIn("await playRouteMemoReady(play.area);", self.page)

    def test_the_lands_motion_lays_nothing(self):
        self.assertIn("function playCellIfLaid(map, x, y) {", self.regions)
        self.assertIn("const cell = playCellIfLaid(map, x, y); if (!cell) continue;", self.page)


class TheWayToTheNextMapTests(unittest.TestCase):
    """The user, 2026-11-20: "一个地图差不多我现在的量多一点之后就要设计next 图 ... 要有一个transition设计stage, 让模型设计一个
    美观的，酷炫的，意想不到的到其它地图的入口（在另一个地图也有对应的入口）"."""

    SVG = ('<svg xmlns="http://www.w3.org/2000/svg"><rect x="0" y="40" width="96" height="40" fill="#336"/>'
           '<circle cx="48" cy="40" r="20" fill="#9cf"/><rect x="40" y="60" width="16" height="20" fill="#222"/></svg>')

    def world_of_two_maps(self, keeper):
        world = keeper.world()
        first = world["regions"][0]
        first.pop("closed", None)
        world["regions"] += [{**first, "id": "r2", "name": "Harbour", "biome": "seaside", "area": "a1"},
                             {**first, "id": "r3", "name": "Sky Town", "biome": "skycity", "area": "a2", "from": None},
                             {**first, "id": "r4", "name": "Moon Dock", "biome": "space", "area": "a3", "from": None}]
        world["commutes"] = [{"from": "r2", "to": "r3", "tie": 0.5, "days": 4}, {"from": "r3", "to": "r4", "tie": 0.2}]
        keeper.save(world)

    def plan(self):
        return {"name": "The Whale Road", "why": "Made up.", "ends": [
            {"map": "a", "land": "r2", "name": "Whale's Mouth", "what": "A great blue whale, its mouth a harbour.",
             "going": "Down its throat like a slide.", "w": 9, "h": 1},
            {"map": "b", "land": "nowhere", "name": "Spout Garden", "what": "A fountain of cloud.", "going": "Up.",
             "w": 4, "h": 3}]}

    def test_a_map_holds_about_what_the_users_does_now_then_the_next_begins(self):
        from personalized_agent.agent import playregions as regions
        self.assertEqual(regions.AREA_REGIONS, 9, "about seven lands now, a little more, then a new map")

    def test_the_design_is_kept_to_one_end_in_each_map_and_a_size_that_can_be(self):
        lands_a, lands_b = [{"id": "r2"}], [{"id": "r3"}]
        kept = muse.read_crossing(self.plan(), lands_a, lands_b)
        self.assertEqual([e["map"] for e in kept["ends"]], ["a", "b"])
        self.assertEqual(kept["ends"][0]["w"], muse.CROSSING_W[1], "too wide: the most")
        self.assertEqual(kept["ends"][0]["h"], muse.CROSSING_H[0], "too low: the least")
        self.assertEqual(kept["ends"][1]["land"], "r3", "a land not of its map: its map's first")
        self.assertIsNone(muse.read_crossing({"ends": [{"map": "a", "what": "x"}]}, lands_a, lands_b), "one end: none")
        self.assertIn("no generic portals", muse.crossing_system())

    def test_each_map_after_the_first_is_joined_once_to_the_map_it_is_most_tied_to(self):
        with tempfile.TemporaryDirectory() as folder:
            said = {muse.TASK_CROSSING: self.plan(), pieces_task(): {"svg": self.SVG, "name": "End", "why": "x"}}
            keeper = keeper_in(folder, said)
            self.world_of_two_maps(keeper)
            self.assertEqual(keeper._design_crossings("2026-11-20"), 2, "a2 and a3, each joined once")
            world = keeper.world()
            ways = {(one["from"], one["to"]) for one in world["crossings"]}
            self.assertEqual(ways, {("a1", "a2"), ("a2", "a3")}, "a2 to a1 (most tied); a3 to a2")
            first = next(one for one in world["crossings"] if one["to"] == "a2")
            self.assertEqual([e["area"] for e in first["ends"]], ["a1", "a2"])
            self.assertTrue(all(e["spec"]["svg"] for e in first["ends"]), "each end drawn as a great piece")
            self.assertEqual(keeper._design_crossings("2026-11-21"), 0, "designed once")

    def test_not_kept_until_both_ends_are_drawn_and_let_be_after_its_tries(self):
        with tempfile.TemporaryDirectory() as folder:
            keeper = keeper_in(folder, {muse.TASK_CROSSING: self.plan()})      # no picture comes back
            self.world_of_two_maps(keeper)
            for _ in range(pw.PlayWorld.CROSSING_TRIES_MOST + 1):
                self.assertEqual(keeper._design_crossings("2026-11-20"), 0)
            world = keeper.world()
            self.assertFalse(world.get("crossings"))
            self.assertEqual(world["crossing_tries"]["a2"], pw.PlayWorld.CROSSING_TRIES_MOST)

    def test_the_page_sets_each_end_by_its_land_and_goes_through_to_the_other(self):
        act = (WEB / "play-act.js").read_text(encoding="utf-8")
        self.assertIn("const at = playCrossingSpot(heart, end.w, end.h, map.crossings);", act)
        self.assertIn("if (way) { playCrossingGo(way); return true; }", act)
        self.assertIn("playTravelArea(other.area, { crossing: one.crossing.id, end: 1 - one.end });", act)
        page = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("for (const one of playCrossings()) if (playPieceInView(one)) playSvgPieceDraw(one, size, now);", page)


def pieces_task():
    from personalized_agent.agent import playpieces
    return playpieces.TASK_PIECE


if __name__ == "__main__":
    unittest.main()

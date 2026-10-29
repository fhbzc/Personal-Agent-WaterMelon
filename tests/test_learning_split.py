"""WHAT IS HANDED OVER, SPLIT AND FOLLOWED (learning/sources.py, the user,
2026-10-28): listed by code a level at a time, judged by the model in kept
rounds - "one piece of work, or one body of reference on a single topic, is
one unit" - each unit kept an item of its own; listed again at each update,
anything new joined to its unit or proposed; a site that limits or refuses
being asked waited for, the parts decided planned first. A large folder is
mapped bottom up instead."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

import support  # noqa: F401

from personalized_agent.data_manage.storage.database import Database
from personalized_agent.learning import online as online_module
from personalized_agent.learning import sources as sources_module
from personalized_agent.learning.items import Items
from personalized_agent.learning.online import Online
from personalized_agent.learning.plan import Planner, split_plan
from personalized_agent.learning.service import Learning
from personalized_agent.learning.sources import (Lister, Sources, Splitter, Waiting,
                                                 github_account, kind_of)
from personalized_agent.learning.store import LearningStore
from personalized_agent.learning.vocab import Vocabulary

ACCOUNT = "https://github.com/quillfern"
API = "https://api.github.com/users/quillfern/repos?per_page=100&type=owner&sort=pushed&page=1"


class _Config(dict):
    def set(self, key, value, internal=False):
        self[key] = value
        return value


class _Response(io.BytesIO):
    def __init__(self, body: str, url: str, headers=None) -> None:
        super().__init__(body.encode("utf-8"))
        self.headers = dict({"Content-Type": "application/json"}, **(headers or {}))
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _repo(name, fork=False, description=""):
    return {"html_url": f"https://github.com/quillfern/{name}", "full_name": f"quillfern/{name}",
            "description": description, "language": "Python", "size": 120, "fork": fork,
            "archived": False, "owner": {"login": "quillfern"}, "pushed_at": "2026-10-01T00:00:00Z"}


class _Web:
    """A made-up web: address -> body, an HTTP code, or (code, headers)."""

    def __init__(self, pages):
        self.pages = dict(pages)
        self.asked = []

    def __call__(self, request, timeout=0):
        url = request.full_url
        self.asked.append((url, request.headers.get("If-none-match", "")))
        said = self.pages.get(url)
        if said is None:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        if isinstance(said, tuple):
            code, headers = said
            raise urllib.error.HTTPError(url, code, "refused", headers, None)
        if isinstance(said, int):
            raise urllib.error.HTTPError(url, said, "refused", {}, None)
        body, headers = said if isinstance(said, list) else (said, {})
        if headers.get("ETag") and request.headers.get("If-none-match") == headers["ETag"]:
            raise urllib.error.HTTPError(url, 304, "not modified", headers, None)
        return _Response(body, url, headers)


class _Model:
    """A scripted model: each call answered by the next reply, and kept."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked = []

    def __call__(self, kind, system, content, max_tokens=0, what=""):
        self.asked.append((kind, content))
        return self.replies.pop(0) if self.replies else {}


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="learning-split-"))
        self.db = Database(self.dir / "agent.db")
        Vocabulary(self.db).ensure_fixed()
        self.config = _Config()
        self.items = Items(self.db, self.config)
        self.sources = Sources(self.db)
        online_module._robots.clear()
        online_module._asked.clear()
        online_module._held_until.clear()
        online_module.SPACING = 0.0

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def splitter(self, web, model, login="quillfern", mapper=None):
        online = Online(self.dir / "online", opener=web)
        return Splitter(self.sources, model, Lister(online, self.items.on_never),
                        logins=lambda: {"github": login}, mapper=mapper)


class WhatSplitsTests(_Base):

    def test_what_may_hold_several_pieces_of_work(self) -> None:
        self.assertEqual(github_account(ACCOUNT), "quillfern")
        self.assertEqual(github_account("https://github.com/quillfern/notes"), "")
        self.assertEqual(github_account("https://github.com/orgs"), "")
        self.assertEqual(kind_of("url", ACCOUNT, {}), "account")
        self.assertEqual(kind_of("url", "https://github.com/a/b", {"repo": "a/b"}), "repo")
        self.assertEqual(kind_of("url", "https://docs.example.org/", {"pages": 40}), "site")
        self.assertEqual(kind_of("url", "https://blog.example.org/post", {"pages": 1}), "")
        self.assertEqual(kind_of("folder", str(self.dir), {}), "folder")
        self.assertEqual(kind_of("text", "", {}), "")


class AnAccountSplitTests(_Base):

    def web(self):
        return _Web({API: json.dumps([
            _repo("tidewatch", description="A tide prediction library"),
            _repo("tidewatch-docs", description="Documentation site for tidewatch"),
            _repo("lanternfish", description="A fork of someone's renderer", fork=True),
            _repo("scratch", description="")])})

    def test_judged_by_the_principle_and_a_fork_never_ticked(self) -> None:
        model = _Model({"units": [
            {"title": "Tidewatch", "about": "The library and its documentation",
             "nodes": ["N1", "N2"], "whose": "mine", "learn": ["cases", "rules"], "tick": True},
            {"title": "Lanternfish", "nodes": ["N3"], "whose": "mine", "tick": True}],
            "skip": [{"node": "N4", "why": "empty"}], "expand": []})
        row = self.splitter(self.web(), model).start("account", ACCOUNT, "quillfern", {})
        self.assertEqual(row["state"], "planned")
        units = row["plan"]["units"]
        self.assertEqual([one["title"] for one in units], ["Tidewatch", "Lanternfish"])
        self.assertEqual([p["name"] for p in units[0]["parts"]],
                         ["quillfern/tidewatch", "quillfern/tidewatch-docs"])
        self.assertEqual((units[0]["whose"], units[0]["tick"]), ("mine", True))
        # A FORK: someone else's, and never ticked by itself.
        self.assertEqual((units[1]["whose"], units[1]["tick"]), ("others", False))
        self.assertEqual(row["plan"]["skipped"][0]["name"], "quillfern/scratch")
        # The model was told the principle and whose the account is.
        kind, shown = model.asked[0]
        self.assertEqual(kind, "split")
        self.assertIn("THE USER'S OWN GITHUB ACCOUNT: quillfern", shown)
        self.assertIn("A FORK", shown)
        self.assertIn("ONE PIECE OF WORK, OR ONE BODY OF REFERENCE ON A SINGLE",
                      sources_module.SPLIT_PROMPT)
        plan = split_plan(row, "url")
        self.assertEqual(plan["units"][0]["more"], ["https://github.com/quillfern/tidewatch-docs"])
        self.assertEqual(plan["units"][0]["scope"], "repo")

    def test_what_the_model_left_out_is_the_users_to_tick(self) -> None:
        model = _Model({"units": [{"title": "Tidewatch", "nodes": ["N1"]}]})
        row = self.splitter(self.web(), model).start("account", ACCOUNT, "", {})
        left = [one for one in row["plan"]["units"] if not one["tick"]]
        self.assertEqual(len(row["plan"]["units"]), 4)
        self.assertEqual(len(left), 3)
        self.assertTrue(all("yours to say" in one["why"] for one in left))

    def test_one_thing_after_all_is_planned_as_before(self) -> None:
        model = _Model({"whole": True, "why": "one project"})
        row = self.splitter(self.web(), model).start("account", ACCOUNT, "", {})
        self.assertTrue(row["plan"]["whole"])
        self.assertIsNone(split_plan(row))


class RoundsTests(_Base):

    def test_inside_asked_for_and_each_round_kept(self) -> None:
        web = _Web({
            API: json.dumps([_repo("harbor"), _repo("kelp")]),
            "https://api.github.com/repos/quillfern/harbor": json.dumps({"default_branch": "main"}),
            "https://api.github.com/repos/quillfern/harbor/git/trees/main": json.dumps({"tree": [
                {"path": "engine", "type": "tree"}, {"path": "viewer", "type": "tree"},
                {"path": "README.md", "type": "blob"}]})})
        model = _Model(
            {"units": [{"title": "Kelp", "nodes": ["N2"], "whose": "mine"}], "expand": ["N1"]},
            # N1: its top-level files (a node of their own since 2026-10-28).
            {"units": [{"title": "Harbor engine", "nodes": ["N2", "N1"]},
                       {"title": "Harbor viewer", "nodes": ["N3"]}]})
        row = self.splitter(web, model).start("account", ACCOUNT, "", {})
        self.assertEqual([one["title"] for one in row["plan"]["units"]],
                         ["Kelp", "Harbor engine", "Harbor viewer"])
        self.assertEqual(len(row["judgment"]["rounds"]), 2)
        self.assertIn("EARLIER ROUNDS' UNITS", model.asked[1][1])
        self.assertIn("quillfern/harbor/engine", model.asked[1][1])
        plan = split_plan(row, "url")
        self.assertTrue(plan["units"][1]["source"].endswith("/tree/main/engine"))

    def test_never_more_than_the_rounds(self) -> None:
        root = self.dir / "deep"
        (root / "one" / "two" / "three" / "four").mkdir(parents=True)
        model = _Model({"expand": ["N1"]}, {"expand": ["N1"]}, {"expand": ["N1"]},
                       {"expand": ["N1"]})
        row = self.splitter(_Web({}), model).start("folder", str(root), "deep", {})
        self.assertEqual(len(row["judgment"]["rounds"]), sources_module.ROUNDS)
        self.assertIn("THE LAST: nothing may be expanded", model.asked[-1][1])
        self.assertNotIn("more inside", model.asked[-1][1])


class WhenASiteHoldsItBackTests(_Base):

    LIMITED = (403, {"X-RateLimit-Remaining": "0",
                     "X-RateLimit-Reset": str(int(time.time()) + 1800)})

    def test_limited_before_anything_waits_and_goes_on_later(self) -> None:
        web = _Web({API: self.LIMITED})
        model = _Model({"units": [{"title": "Tidewatch", "nodes": ["N1"]},
                                  {"title": "Kelp", "nodes": ["N2"]}]})
        splitter = self.splitter(web, model)
        row = splitter.start("account", ACCOUNT, "", {})
        self.assertEqual(row["state"], "waiting")
        self.assertGreater(row["waiting_until"], time.time() + 1000, "the time GitHub gave")
        self.assertIn("limits how often", row["why"])
        # NOT ASKED AGAIN BEFORE THEN - nothing is sent.
        asked = len(web.asked)
        self.assertEqual(splitter.go(row["id"])["state"], "waiting")
        self.assertEqual(len(web.asked), asked)
        self.assertEqual(model.asked, [], "the model is not asked without a listing")
        # The time come: listed, judged, planned.
        online_module._held_until.clear()
        web.pages[API] = json.dumps([_repo("tidewatch"), _repo("kelp")])
        row = splitter.go(row["id"])
        self.assertEqual(row["state"], "planned")
        self.assertEqual(len(row["plan"]["units"]), 2)

    def test_the_decided_parts_first_the_rest_later(self) -> None:
        web = _Web({
            API: json.dumps([_repo("harbor"), _repo("kelp")]),
            "https://api.github.com/repos/quillfern/harbor": self.LIMITED})
        model = _Model({"units": [{"title": "Kelp", "nodes": ["N2"]}], "expand": ["N1"]})
        row = self.splitter(web, model).start("account", ACCOUNT, "", {})
        self.assertEqual(row["state"], "waiting")
        self.assertEqual([one["title"] for one in row["plan"]["units"]], ["Kelp"])
        self.assertEqual(row["plan"]["later"]["count"], 1)
        self.assertEqual(row["plan"]["later"]["names"], ["quillfern/harbor"])
        self.assertTrue(row["plan"]["later"]["until"])
        self.assertEqual(len(row["judgment"]["rounds"]), 1, "the round is kept")

    def test_a_refusal_waits_a_day_and_is_blocked_after_some(self) -> None:
        web = _Web({API: 403})
        splitter = self.splitter(web, _Model())
        row = splitter.start("account", ACCOUNT, "", {})
        self.assertEqual(row["state"], "waiting")
        self.assertGreater(row["waiting_until"], time.time() + 80_000)
        for _ in range(sources_module.BLOCKED_AFTER):
            row = splitter.go(row["id"])
        self.assertEqual(row["state"], "blocked")

    def test_a_site_that_does_not_answer_waits_longer_each_time(self) -> None:
        web = _Web({API: 503})
        splitter = self.splitter(web, _Model())
        row = splitter.start("account", ACCOUNT, "", {})
        first = row["waiting_until"] - time.time()
        row = splitter.go(row["id"])
        self.assertGreater(row["waiting_until"] - time.time(), first + 100)


class TheSitesLimitsTests(_Base):

    def test_an_hours_budget_and_an_unchanged_listing_at_no_cost(self) -> None:
        url = "https://api.github.com/users/quillfern/repos"
        web = _Web({url: ["[]", {"ETag": '"v1"'}]})
        online = Online(self.dir / "online", opener=web)
        first = online._get(url, "application/vnd.github+json")
        self.assertEqual(first.get("raw"), "[]")
        again = online._get(url, "application/vnd.github+json")
        self.assertTrue(again.get("not_modified"))
        self.assertEqual(again.get("raw"), "[]", "the copy kept")
        self.assertEqual(web.asked[-1][1], '"v1"')
        # A 304 is not counted against the hour.
        self.assertEqual(len(online_module._asked["api.github.com"]), 1)
        online_module._asked["api.github.com"] = [time.time()] * online_module.HOURLY["api.github.com"]
        sent = len(web.asked)
        held = online._get(url + "?page=2", "application/vnd.github+json")
        self.assertEqual(held.get("wait"), "limited")
        self.assertEqual(len(web.asked), sent, "not sent")


class AFolderSplitTests(_Base):

    def make(self, *names):
        root = self.dir / "work"
        for name in names:
            (root / name).mkdir(parents=True, exist_ok=True)
            (root / name / "notes.md").write_text("draft", encoding="utf-8")
        return root

    def test_without_the_engine_judged_from_its_top_its_own_files_a_node(self) -> None:
        root = self.make("survey-2026", "survey-2026-figures", "recipes")
        (root / "budget.xlsx").write_text("x", encoding="utf-8")
        model = _Model({"units": [{"title": "The survey", "nodes": ["N3", "N4"]},
                                  {"title": "Recipes", "nodes": ["N2"]},
                                  {"title": "Its own files", "nodes": ["N1"]}]})
        row = self.splitter(_Web({}), model).start("folder", str(root), "work", {})
        self.assertIn("work - its own files", model.asked[0][1], "never left out unseen")
        self.assertIn("budget.xlsx", model.asked[0][1])
        own = row["plan"]["units"][2]["parts"][0]
        self.assertTrue(own["source"].endswith("|files"))
        self.assertEqual(row["plan"]["units"][0]["whose"], "mine")

    def test_every_folder_mapped_bottom_up_whatever_its_size(self) -> None:
        root = self.make("run-001", "run-002", "recipes")
        (root / "budget.xlsx").write_text("x", encoding="utf-8")
        mapped = []

        def mapper(path):
            mapped.append(path)
            return [{"key": "k", "title": "The runs", "summary": "one experiment",
                     "folders": [str(root / "run-001"), str(root / "run-002")]},
                    {"key": "r", "title": "recipes", "folders": [str(root / "recipes")]},
                    {"key": str(root) + "|files", "title": "work", "only_files": True,
                     "summary": "the files directly in work", "folders": [str(root)]}]
        model = _Model()
        row = self.splitter(_Web({}), model, mapper=mapper).start("folder", str(root), "work", {})
        self.assertEqual(mapped, [str(root)])
        self.assertEqual(model.asked, [], "not judged from the top")
        self.assertTrue(row["judgment"]["bottom_up"])
        units = row["plan"]["units"]
        self.assertEqual([one["title"] for one in units], ["The runs", "recipes", "work"])
        self.assertEqual(units[2]["parts"][0]["source"], str(root) + "|files")
        # Kept as an item: its own files, read without the folders in it.
        item = self.items.add(dict(split_plan(row, "folder")["units"][2]))
        from personalized_agent.learning.items import folder_parts
        self.assertEqual(folder_parts(item), ([str(root)], [str(root)]))

    def test_one_thing_mapped_is_planned_as_before(self) -> None:
        root = self.make("a", "b")
        row = self.splitter(_Web({}), _Model(), mapper=lambda path: [
            {"key": path, "title": "work", "folders": [path]}]).start("folder", str(root), "", {})
        self.assertTrue(row["plan"]["whole"])
        self.assertIsNone(split_plan(row))

    def test_followed_with_the_engine(self) -> None:
        root = self.make("run-001", "recipes")
        maps = [[{"key": "k", "title": "The runs", "folders": [str(root / "run-001")]},
                 {"key": "r", "title": "recipes", "folders": [str(root / "recipes")]}]]
        learning = Learning(self.db, self.config, None, self.dir / "data")
        learning._splitter = lambda _ask: self.splitter(_Web({}), _Model(),
                                                        mapper=lambda path: maps[-1])
        learning._check = lambda _update: None
        row = learning._splitter(None).start("folder", str(root), "work", {})
        learning.add_split(row["id"], split_plan(row, "folder")["units"])
        # Since: a second run (part of the runs), a new project, recipes gone.
        (root / "run-002").mkdir()
        (root / "garden").mkdir()
        shutil.rmtree(root / "recipes")
        maps.append([{"key": "k", "title": "The runs",
                      "folders": [str(root / "run-001"), str(root / "run-002")]},
                     {"key": "g", "title": "garden", "folders": [str(root / "garden")]}])
        self.sources.save(dict(self.sources.get(row["id"]), listed_at=""))
        learning._follow_sources("u", None)
        runs = next(one for one in self.items.all() if one["title"] == "The runs")
        self.assertEqual(runs["more"], [str(root / "run-002")], "joined, no question asked")
        recipes = next(one for one in self.items.all() if one["title"] == "recipes")
        self.assertEqual(recipes["status"], "unreachable")
        # A new project: kept as judged, nothing waiting for the user
        # (2026-10-29).
        self.assertIn("garden", [one["title"] for one in self.items.all()])
        self.assertEqual(self.sources.get(row["id"])["proposals"], [])


class TheTopLevelIsNeverLeftOutTests(_Base):
    """A level's own content - a repository's top-level files, a site's
    front page - is a node of its own (the gap, 2026-10-28)."""

    def test_a_repositorys_top_level_files(self) -> None:
        web = _Web({
            "https://api.github.com/repos/quillfern/harbor": json.dumps({"default_branch": "main"}),
            "https://api.github.com/repos/quillfern/harbor/git/trees/main": json.dumps({"tree": [
                {"path": "engine", "type": "tree"}, {"path": "README.md", "type": "blob"},
                {"path": "pyproject.toml", "type": "blob"}]})})
        model = _Model({"units": [{"title": "Harbor", "nodes": ["N1", "N2"]}]})
        row = self.splitter(web, model).start("repo", "https://github.com/quillfern/harbor",
                                              "harbor", {})
        shown = model.asked[0][1]
        self.assertIn("the files at its top level", shown)
        self.assertIn("README.md", shown)
        parts = row["plan"]["units"][0]["parts"]
        self.assertTrue(parts[0]["source"].endswith("/tree/main?files=top"))
        # Read: its top-level files only.
        web.pages["https://api.github.com/repos/quillfern/harbor/git/trees/main?recursive=1"] = \
            json.dumps({"tree": [{"path": "README.md", "type": "blob"},
                                 {"path": "engine/core.py", "type": "blob"}]})
        web.pages["https://raw.githubusercontent.com/quillfern/harbor/main/README.md"] = "# Harbor"
        pages = Online(self.dir / "o2", opener=web).pages(
            {"source": parts[0]["source"], "scope": "repo", "whose": "others"})
        self.assertIn("README.md", [one.get("title") for one in pages])
        self.assertNotIn("engine/core.py", [one.get("title") for one in pages])

    def test_a_sites_front_page(self) -> None:
        start = "https://docs.example.org/"
        web = _Web({start: ['<html><title>Tide docs</title><body>Welcome to the tide docs.'
                            '<a href="/guide/one">g</a><a href="/api/two">a</a></body></html>',
                            {"Content-Type": "text/html"}]})
        model = _Model({"units": [{"title": "Tide docs", "nodes": ["N1", "N2", "N3"]}]})
        row = self.splitter(web, model).start("site", start, "", {})
        self.assertIn("N1: the front page", model.asked[0][1])
        self.assertTrue(row["plan"]["units"][0]["parts"][0]["source"].endswith("#front-page"))


class TheDaysLimitsTests(unittest.TestCase):
    """THE LIMITS SET IN THE PANEL ARE THE ONES HELD TO (the user, 2026-10-28):
    from the panel's request through the route to the setting, to what an
    update and a plan do - input and output apart, input with the cache's
    reads and writes; none by default."""

    def setUp(self) -> None:
        from support import TemporaryAgent
        from personalized_agent.api.routes import build_routes
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.routes = build_routes(self.app)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def post(self, body):
        class Req:
            def get(self, key, default=None):
                return body.get(key, default)

            def require(self, key):
                return body[key]
        return self.routes[("POST", "/api/learning")](Req())

    def spend(self, fresh=0, cached=0, written=0, output=0):
        self.app.model_calls.record(provider="claude_code", model="m", task="learning_read",
                                    input_tokens=fresh, output_tokens=output, latency_ms=1,
                                    cached_input_tokens=cached, cache_written_tokens=written)

    def test_none_by_default_and_each_limit_held_to(self) -> None:
        learning = self.app.learning
        self.assertEqual(learning.status()["daily_limits"], {"input": 0, "output": 0})
        self.spend(fresh=10, cached=5_000_000, output=900_000)
        self.assertFalse(learning._over_limit(), "no limit: never over")
        # INPUT, set as the panel sets it: the cache's reads count.
        said = self.post({"action": "auto", "daily_input": 1_000_000})
        self.assertEqual(said["daily_limits"], {"input": 1_000_000, "output": 0})
        self.assertEqual(self.app.config.get("learning_daily_input_tokens"), 1_000_000)
        self.assertTrue(learning._over_limit())
        parts = said["tokens_today_parts"]
        self.assertEqual((parts["fresh"], parts["cached"], parts["output"]),
                         (10, 5_000_000, 900_000))
        # What it stops: Update now, an update's next call, a plan.
        with self.assertRaises(Exception):
            self.post({"action": "update"})
        update_id = learning.store.save_update({"cutoff": "2026-10-28T00:00:00+00:00",
                                               "by": "you", "state": "running", "plan": {}})
        self.assertTrue(learning._asker(update_id).stopped())
        self.assertEqual(learning.store.update(update_id)["state"], "waiting")
        draft = learning.planner.start({"kind": "text", "text": "Short sentences."}, wait=True)
        self.assertEqual((draft["state"], draft.get("limit")), ("waiting", True))
        # OUTPUT on its own.
        self.post({"action": "auto", "daily_input": 0, "daily_output": 1_000_000})
        self.assertFalse(learning._over_limit(), "900,000 of 1,000,000 out")
        self.spend(output=200_000)
        self.assertTrue(learning._over_limit())
        # Both 0 again: no limit.
        self.post({"action": "auto", "daily_output": 0})
        self.assertFalse(learning._over_limit())

    def test_the_panel_sends_what_the_route_takes(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                  / "app.js").read_text(encoding="utf-8")
        self.assertIn('action: "auto", daily_input:', script)
        self.assertIn('action: "auto", daily_output:', script)
        self.assertIn("s.daily_limits", script)
        self.assertNotIn("daily_tokens", script)


class FollowedTests(_Base):

    def _learning(self, web, model):
        learning = Learning(self.db, self.config, None, self.dir / "data")
        learning._splitter = lambda _ask: self.splitter(web, model)
        learning._check = lambda _update: None
        return learning

    def test_new_joined_or_proposed_and_gone_said(self) -> None:
        web = _Web({API: json.dumps([_repo("tidewatch"), _repo("kelp")])})
        model = _Model({"units": [{"title": "Tidewatch", "nodes": ["N1"], "whose": "mine"},
                                  {"title": "Kelp", "nodes": ["N2"], "whose": "mine"}]})
        learning = self._learning(web, model)
        row = learning._splitter(None).start("account", ACCOUNT, "quillfern", {})
        plan = split_plan(row, "url")
        said = learning.add_split(row["id"], plan["units"])
        self.assertEqual(len(said["items"]), 2)
        self.assertTrue(all(one["source_id"] == row["id"] for one in said["items"]))
        self.assertEqual(self.sources.get(row["id"])["state"], "following")
        # Since: a docs repository (part of Tidewatch), a new project, and
        # Kelp gone.
        web.pages[API] = json.dumps([_repo("tidewatch"), _repo("tidewatch-docs"),
                                     _repo("anchorline")])
        model.replies = [{"join": [{"node": "N1", "unit": "U1"}],
                          "new": [{"title": "Anchorline", "nodes": ["N2"], "whose": "mine"}]}]
        self.sources.save(dict(self.sources.get(row["id"]), listed_at=""))
        learning._follow_sources("u", model)
        tide = next(one for one in self.items.all() if one["title"] == "Tidewatch")
        self.assertEqual(tide["more"], ["https://github.com/quillfern/tidewatch-docs"],
                         "joined, no question asked")
        kelp = next(one for one in self.items.all() if one["title"] == "Kelp")
        self.assertEqual(kelp["status"], "unreachable")
        self.assertIn("no longer in", kelp["note"])
        # A new unit: KEPT AS JUDGED - an item of its own, nothing waiting
        # for the user (the user, 2026-10-29).
        self.assertIn("Anchorline", [one["title"] for one in self.items.all()])
        self.assertEqual(self.sources.get(row["id"])["proposals"], [])
        self.assertEqual(learning.lessons()["sources"][0]["proposals"], [])

    def test_a_new_unit_judged_not_worth_learning_is_not_added(self) -> None:
        web = _Web({API: json.dumps([_repo("tidewatch"), _repo("kelp")])})
        model = _Model({"units": [{"title": "Tidewatch", "nodes": ["N1"], "whose": "mine"},
                                  {"title": "Kelp", "nodes": ["N2"], "whose": "mine"}]})
        learning = self._learning(web, model)
        row = learning._splitter(None).start("account", ACCOUNT, "quillfern", {})
        learning.add_split(row["id"], split_plan(row, "url")["units"])
        web.pages[API] = json.dumps([_repo("tidewatch"), _repo("kelp"),
                                     _repo("scratchpad-junk")])
        model.replies = [{"join": [], "new": [{"title": "Junk", "nodes": ["N1"],
                                                "tick": False, "why": "an empty test"}]}]
        self.sources.save(dict(self.sources.get(row["id"]), listed_at=""))
        decided = []
        keep = learning.keep_proposal
        learning.keep_proposal = lambda source, ident, kept: (
            decided.append(kept) or keep(source, ident, kept))
        learning._follow_sources("u", model)
        self.assertEqual(decided, [False], "found, and not kept, as judged")
        self.assertNotIn("Junk", [one["title"] for one in self.items.all()])
        self.assertEqual(self.sources.get(row["id"])["proposals"], [])

    def test_a_whole_source_deleted(self) -> None:
        web = _Web({API: json.dumps([_repo("tidewatch"), _repo("kelp")])})
        model = _Model({"units": [{"title": "Tidewatch", "nodes": ["N1"]},
                                  {"title": "Kelp", "nodes": ["N2"]}]})
        learning = self._learning(web, model)
        row = learning._splitter(None).start("account", ACCOUNT, "", {})
        learning.add_split(row["id"], split_plan(row, "url")["units"])
        learning.delete_source(row["id"])
        self.assertEqual(self.items.all(), [])
        self.assertIsNone(self.sources.get(row["id"]))
        self.assertTrue(self.items.on_never(ACCOUNT))
        self.assertTrue(self.items.on_never("https://github.com/quillfern/kelp"))


class LearnedAtOnceTests(_Base):
    """HANDED OVER, LEARNED AT ONCE - no plan to check (the user,
    2026-10-29): an address and what to learn of it; the plan added as soon
    as it is ready, the user's words its angle and the split's guide; one
    held back added when it goes on."""

    def _learning(self, web, model):
        learning = Learning(self.db, self.config, None, self.dir / "data")
        learning.planner.online = Online(self.dir / "o", web)
        learning.planner.ask = model
        learning.planner.make_ask = None
        learning._splitter = lambda _ask: self.splitter(web, model)
        learning.planner.make_splitter = learning._splitter
        learning.update_now = lambda *a, **k: None
        return learning

    def test_a_split_source_added_and_judged_by_the_words(self) -> None:
        web = _Web({API: json.dumps([_repo("tidewatch"), _repo("kelp")])})
        model = _Model({"units": [{"title": "Tidewatch", "nodes": ["N1"]},
                                  {"title": "Kelp", "nodes": ["N2"], "tick": False,
                                   "why": "not about tides"}]})
        learning = self._learning(web, model)
        words = "Only how the tide tables are computed."
        draft = learning.planner.start({"kind": "url", "source": ACCOUNT, "auto": True,
                                        "instruction": words}, wait=True)
        self.assertEqual(draft["state"], "added", draft)
        self.assertEqual([one["title"] for one in draft["added"]["items"]], ["Tidewatch"],
                         "what it judged worth learning, and only that")
        item = self.items.all()[0]
        self.assertEqual(item["angle"], words, "the user's words, verbatim")
        self.assertIn(words, model.asked[0][1], "the split judged by them")

    def test_one_thing_added_its_angle_the_users_words(self) -> None:
        page = "https://notes.example.org/tides"
        web = _Web({page: ["<html><title>Tides</title><body>On tides.</body></html>",
                           {"Content-Type": "text/html"}]})
        model = _Model({"title": "Tides", "about": "a note", "whose": "others",
                        "learn": ["reference"], "angle": "the model's own", "scope": "page",
                        "cadence": "once"})
        learning = self._learning(web, model)
        learning.planner.make_splitter = None
        learning.planner.splitter = None
        words = "How the note explains spring tides."
        draft = learning.planner.start({"kind": "url", "source": page, "auto": True,
                                        "instruction": words}, wait=True)
        self.assertEqual(draft["state"], "added", draft)
        self.assertEqual(self.items.all()[0]["angle"], words)
        self.assertIn(words, model.asked[-1][1])

    def test_asked_for_a_plan_to_check_it_is_only_drafted(self) -> None:
        page = "https://notes.example.org/tides"
        web = _Web({page: ["<html><title>Tides</title><body>On tides.</body></html>",
                           {"Content-Type": "text/html"}]})
        learning = self._learning(web, _Model({"title": "Tides", "scope": "page"}))
        learning.planner.make_splitter = None
        learning.planner.splitter = None
        draft = learning.planner.start({"kind": "url", "source": page}, wait=True)
        self.assertEqual(draft["state"], "ready")
        self.assertEqual(self.items.all(), [])

    def test_over_the_days_limit_added_as_it_is_and_read_later(self) -> None:
        learning = self._learning(_Web({}), _Model({}))
        learning.planner.over_limit = lambda: True
        draft = learning.planner.start({"kind": "text", "text": "Short sentences, always.",
                                        "auto": True, "instruction": "the style"}, wait=True)
        self.assertEqual(draft["state"], "added", draft)
        item = self.items.all()[0]
        self.assertEqual((item["angle"], item["kind"]), ("the style", "text"))

    def test_held_back_by_a_site_added_when_it_goes_on(self) -> None:
        web = _Web({API: json.dumps([_repo("tidewatch"), _repo("kelp")])})
        model = _Model({"units": [{"title": "Tidewatch", "nodes": ["N1"]},
                                  {"title": "Kelp", "nodes": ["N2"]}]})
        learning = self._learning(web, model)
        # As a site held it back before anything was listed: kept, waiting,
        # nothing judged yet - as Splitter.start keeps it.
        self.sources.save({
            "id": "S-held", "kind": "account", "source": ACCOUNT, "title": "quillfern",
            "state": "waiting", "why": "the site asked to wait", "waiting_until": 0,
            "attempts": 1, "tree": {"root": ACCOUNT, "nodes": {ACCOUNT: {
                "id": ACCOUNT, "name": "quillfern", "kind": "account", "source": ACCOUNT,
                "size": 0, "peek": "", "owner": "quillfern", "fork": False,
                "archived": False, "more": True, "children": None}}},
            "judgment": {"rounds": [], "units": [], "skip": [], "expand": [ACCOUNT],
                         "later": []},
            "plan": {}, "proposals": [], "listed_at": "",
            "handed": {"kind": "url", "source": ACCOUNT, "auto": True}})
        self.assertEqual(self.items.all(), [])
        learning.resume_sources()
        self.assertEqual(sorted(one["title"] for one in self.items.all()), ["Kelp", "Tidewatch"])


class ThePlannerSplitsTests(_Base):

    def test_a_split_plan_or_a_waiting_one(self) -> None:
        web = _Web({API: json.dumps([_repo("tidewatch"), _repo("kelp")]),
                    ACCOUNT: "<html><title>quillfern</title><body>profile</body></html>"})
        model = _Model({"units": [{"title": "Tidewatch", "nodes": ["N1"]},
                                  {"title": "Kelp", "nodes": ["N2"]}]})
        planner = Planner(self.db, model, self.dir / "online", online=Online(self.dir / "o", web))
        planner.splitter = self.splitter(web, model)
        draft = planner.start({"kind": "url", "source": ACCOUNT}, wait=True)
        self.assertEqual(draft["state"], "ready")
        plan = draft["plan"]
        self.assertTrue(plan["split"])
        self.assertEqual([one["title"] for one in plan["units"]], ["Tidewatch", "Kelp"])
        # Each unit a plan an item is made from, with its source.
        item = self.items.add(dict(plan["units"][0]))
        self.assertEqual(item["source_id"], plan["source_id"])
        # Held back before anything: the draft waits, the source is kept.
        self.sources.remove(plan["source_id"])
        web.pages[API] = WhenASiteHoldsItBackTests.LIMITED
        online_module._held_until.clear()
        draft = planner.start({"kind": "url", "source": ACCOUNT}, wait=True)
        self.assertEqual(draft["state"], "waiting")
        self.assertTrue(draft["source_id"])


class ThePageTests(unittest.TestCase):
    """The split plan drawn and changed in a real browser: joined, split,
    a part put back, ticks read; a source's row with what is new in it."""

    def test_the_plan_and_the_source_row(self) -> None:
        import html as html_module
        import os
        import re
        import shutil as sh
        import subprocess
        engine = next((one for one in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            if os.path.exists(one)), None) or sh.which("google-chrome") or sh.which("chromium")
        if not engine:
            self.skipTest("a browser is needed to draw the plan")
        script = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                  / "app.js").read_text(encoding="utf-8")
        start = script.index("const SPLIT_SCOPE")
        code = script[start:script.index("function learnRead() {", start)]
        start = script.index("const SOURCE_KIND")
        code += script[start:script.index("/* WHAT WAITS FOR YOU", start)]
        plan = {"split": True, "source_id": "S-1", "kind": "url", "source": ACCOUNT,
                "cadence": "update", "later": {"count": 1, "until": ""},
                "skipped": [{"source": "https://github.com/quillfern/scratch",
                             "name": "quillfern/scratch", "kind": "repo", "why": "empty"}],
                "units": [
                    {"title": "Tidewatch", "whose": "mine", "tick": True, "estimate": 100,
                     "learn": ["cases"], "parts": [
                         {"source": "https://github.com/quillfern/tidewatch",
                          "name": "quillfern/tidewatch", "kind": "repo"}]},
                    {"title": "Kelp & docs", "whose": "others", "tick": True, "estimate": 200,
                     "parts": [{"source": "https://github.com/quillfern/kelp",
                                "name": "quillfern/kelp", "kind": "repo"},
                               {"source": "https://github.com/quillfern/kelp-docs",
                                "name": "quillfern/kelp-docs", "kind": "repo"}]}]}
        source = {"id": "S-1", "kind": "account", "source": ACCOUNT, "title": "quillfern",
                  "state": "waiting", "why": "", "waiting_until": "", "followed": True,
                  "ready": False, "listed_at": "", "proposals": [
                      {"id": "U-9", "title": "<b>Anchor</b>", "whose": "mine", "parts": []}]}
        page = ("<!doctype html><meta charset='utf-8'><title></title><div id='learn-body'></div>"
                "<input id='x'><script>window.$=q=>document.querySelector(q);"
                "window.escapeHtml=s=>String(s).replace(/[&<>\\\"']/g,c=>({'&':'&amp;','<':'&lt;',"
                "'>':'&gt;','\\\"':'&quot;',\"'\":'&#39;'}[c]));"
                "window.localStamp=s=>s;window.learnButtons=()=>{};window.toast=()=>{};"
                "window.learningState={removing:null};window.learnState={};</script><script>"
                + code + "</script><script>window.addEventListener('load',()=>{let error='';"
                "let out={};try{learnShowSplit(" + json.dumps(plan) + ");"
                "out.first=document.querySelectorAll('[data-split-unit]').length;"
                "document.querySelector('[data-split-join=\"1\"]').click();"
                "out.joined=document.querySelectorAll('[data-split-unit]').length;"
                "document.querySelector('[data-split-apart=\"0\"]').click();"
                "out.apart=document.querySelectorAll('[data-split-unit]').length;"
                "document.querySelector('[data-split-unskip=\"0\"]').click();"
                "out.unskipped=document.querySelectorAll('[data-split-unit]').length;"
                "const ticks=document.querySelectorAll('[data-split-tick]');ticks[1].checked=false;"
                "document.querySelectorAll('[data-split-title]')[0].value='Tide library';"
                "learnSplitSync();out.units=learnSplitUnits(learnState.plan);"
                "out.row=learningSourceRow(" + json.dumps(source) + ",[]);"
                "}catch(e){error=e.message;}"
                "document.title=JSON.stringify({error,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page, encoding="utf-8")
            done = subprocess.run([engine, "--headless=new", "--disable-gpu", "--no-first-run",
                                   f"--user-data-dir={work}/profile",
                                   "--virtual-time-budget=2000", "--dump-dom", probe.as_uri()],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=120)
        match = re.search(r"<title>(.*?)</title>", done.stdout, re.S)
        self.assertIsNotNone(match, done.stderr[-1500:])
        said = json.loads(html_module.unescape(match.group(1)))
        self.assertEqual(said["error"], "")
        out = said["out"]
        self.assertEqual((out["first"], out["joined"], out["apart"], out["unskipped"]), (2, 1, 3, 4))
        units = out["units"]
        self.assertEqual(len(units), 3, "one unticked")
        self.assertEqual(units[0]["title"], "Tide library")
        self.assertEqual(units[0]["scope"], "repo")
        self.assertTrue(all(one["kind"] == "url" and not one["more"] for one in units))
        self.assertIn("quillfern/scratch", [one["source"].split("github.com/")[1] for one in units])
        self.assertIn("Waiting for the site", out["row"])
        self.assertIn("data-learn-proposal-keep", out["row"])
        self.assertNotIn("<b>Anchor</b>", out["row"], "escaped")


if __name__ == "__main__":
    unittest.main()

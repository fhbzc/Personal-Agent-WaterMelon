"""The user's connections (heartbeat/connections/): the people around them,
what they are doing, and what of it the user needs to know.

The user's design: step 2 of the agent heartbeat finds the people in what the
user had in front of them; each state heartbeat round works out what their
current work needs to know of the people - searching the contacts, looking
up on the public web one whose news is old or one important and long unseen
- for the Overview, never the calendar. The user's own changes to their
contacts are the connection lessons: kept word for word up to a hundred,
the older summarised, given dated to every add and update - a person the
user deleted is not added back without strong newer evidence, said why. All
of it goes into the lessons package.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.heartbeat.connections import people as people_step
from personalized_agent.heartbeat.connections.round import ConnectionsRound, work_context
from personalized_agent.heartbeat.connections.store import KEEP_ACTIONS, ConnectionsStore
from personalized_agent.models.base import ModelResponse, ModelUsage, ToolCall


class _Request:
    def __init__(self, body):
        self.body = body

    def get(self, key, default=None):
        return self.body.get(key, default)

    def require(self, key):
        from personalized_agent.api.errors import ApiError
        if self.body.get(key) in (None, ""):
            raise ApiError(f"missing parameter: {key}")
        return self.body[key]


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.store: ConnectionsStore = self.app.connections

    def tearDown(self) -> None:
        self.app.connections_round.wait(10)
        self.agent.cleanup()


class TheStoreAndTheLessonsTests(_Base):

    def test_each_change_is_recorded_by_whom_with_its_reason(self) -> None:
        mei = self.store.add("Mei Tan", by="user", reason="my advisor",
                             relationship="mentor", org="UCLA", tags="research, thesis")
        self.assertEqual(mei["tags"], ["research", "thesis"])
        self.store.edit(mei["id"], by="user", reason="moved", org="Stanford")
        self.store.delete(mei["id"], by="user", reason="not in touch any more")
        actions = self.store.actions("user")
        self.assertEqual([one["action"] for one in actions], ["delete", "edit", "add"])
        self.assertEqual(actions[1]["before"], {"org": "UCLA"})
        self.assertEqual(actions[1]["after"], {"org": "Stanford"})
        self.assertEqual(actions[0]["reason"], "not in touch any more")
        text = self.store.changes_text()
        self.assertIn("delete  Mei Tan", text)
        self.assertIn("the user's reason: not in touch any more", text)
        self.assertIn(actions[0]["at"][:10], text)
        # Kept, marked deleted - not gone.
        self.assertTrue(self.store.get(mei["id"])["deleted"])
        self.assertNotIn(mei["id"], [one["id"] for one in self.store.people()])

    def test_on_a_contact_the_agent_may_overwrite_what_the_user_set(self) -> None:
        # The user's decision: on anyone's profile but their own, what the
        # agent found replaces what they wrote; their change stays in history.
        ana = self.store.add("Ana", by="user", relationship="friend")
        self.store.edit(ana["id"], by="agent", relationship="colleague", org="Acme",
                        tags=["climbing"])
        kept = self.store.get(ana["id"])
        self.assertEqual(kept["relationship"], "colleague")
        self.assertEqual(kept["relationships"], ["colleague", "friend"])
        self.assertEqual(kept["org"], "Acme")
        self.assertEqual(kept["tags"], ["climbing"])
        self.store.edit(ana["id"], by="agent", tags=["piano"])
        self.assertEqual(self.store.get(ana["id"])["tags"], ["climbing", "piano"])
        self.assertEqual(self.store.actions("user")[-1]["action"], "add", "kept in history")

    def test_on_their_own_profile_what_the_user_set_stays(self) -> None:
        me = self.store.me()
        self.store.edit(me["id"], by="user", profession="researcher", org="UCLA")
        me = self.store.edit(me["id"], by="agent", profession="engineer", org="Acme")
        self.assertEqual((me["profession"], me["org"]), ("researcher", "UCLA"))

    def test_past_a_hundred_the_oldest_are_summarised(self) -> None:
        person = self.store.add("P", by="user")
        for n in range(KEEP_ACTIONS + 4):
            self.store.edit(person["id"], by="user", summary=f"note {n}")
        overflow = self.store.overflowing_actions()
        self.assertEqual(len(overflow), 5)
        self.assertEqual(overflow[0]["action"], "add", "oldest first")
        self.store.fold_actions(overflow, "Early on the user kept notes on P.")
        self.assertEqual(len(self.store.actions("user", 1000)), KEEP_ACTIONS)
        self.assertEqual(self.store.overflowing_actions(), [])
        text = self.store.changes_text()
        self.assertIn("Earlier (5 changes", text)
        self.assertIn("Early on the user kept notes on P.", text)

    def test_merge_and_restore(self) -> None:
        a = self.store.add("Mei Tan", by="user")
        b = self.store.add("谭梅", by="agent")
        self.store.add_account(b["id"], "wechat", "tanmei88")
        self.store.add_activity(b["id"], "Sent the draft", "2026-10-01T10:00:00+08:00")
        kept = self.store.merge(a["id"], b["id"], by="user", reason="same person")
        self.assertIn("谭梅", kept["aliases"])
        self.assertEqual(self.store.accounts(a["id"])[0]["handle"], "tanmei88")
        self.assertEqual(self.store.activity(a["id"])[0]["what"], "Sent the draft")
        self.assertIsNone(self.store.get(b["id"]))
        self.assertEqual(self.store.find("谭梅")["id"], a["id"])
        self.assertEqual(self.store.find("", [{"platform": "WeChat", "handle": "TanMei88"}])["id"],
                         a["id"])


class SeveralJobsAndPlacesTests(_Base):
    """A person may have several jobs and places, as several relationships:
    a list, its first the main one - "profession" and "location" are always
    the main one of "professions" and "locations"."""

    def test_the_users_list_and_the_agents_one_after_it(self) -> None:
        mei = self.store.add("Mei Tan", by="user", professions=["professor", "founder"],
                             locations=["Lisbon", "Beijing"])
        self.assertEqual((mei["profession"], mei["professions"]), ("professor",
                                                                   ["professor", "founder"]))
        self.assertEqual((mei["location"], mei["locations"]), ("Lisbon",
                                                               ["Lisbon", "Beijing"]))
        # The agent finds another: on a contact, the main one now - the
        # user's kept after it.
        found = self.store.edit(mei["id"], by="agent", profession="advisor",
                                location="Shanghai")
        self.assertEqual(found["professions"], ["advisor", "professor", "founder"])
        self.assertEqual(found["profession"], "advisor")
        self.assertEqual(found["locations"], ["Shanghai", "Lisbon", "Beijing"])
        # The user makes another the main one.
        mine = self.store.edit(mei["id"], by="user", professions=["founder", "professor"])
        self.assertEqual(mine["profession"], "founder")
        # Found by any of them.
        self.assertEqual([one["id"] for one in self.store.search("Beijing")], [mei["id"]])

    def test_merged_both_are_kept(self) -> None:
        keep = self.store.add("Mei Tan", by="user", profession="professor", location="Lisbon")
        other = self.store.add("谭梅", by="agent", profession="founder", location="Beijing")
        said = self.store.merge_preview(keep["id"], other["id"])
        self.assertEqual((said["professions"], said["locations"]), (["founder"], ["Beijing"]))
        kept = self.store.merge(keep["id"], other["id"], by="user")
        self.assertEqual(kept["professions"], ["professor", "founder"])
        self.assertEqual(kept["locations"], ["Lisbon", "Beijing"])
        self.assertEqual((kept["profession"], kept["location"]), ("professor", "Lisbon"))

    def test_people_from_before_have_theirs_as_a_list(self) -> None:
        old = self.store.add("Ana", by="user", profession="doctor", location="Lisbon")
        self.app.db.execute("UPDATE connection_people SET professions = '[]', "
                            "locations = '' WHERE id = ?", (old["id"],))
        self.store.ensure()
        again = self.store.get(old["id"])
        self.assertEqual((again["professions"], again["locations"]), (["doctor"], ["Lisbon"]))


class MergingIsPreviewedTests(_Base):

    def test_what_would_come_over_then_it_does(self) -> None:
        keep = self.store.add("Mei Tan", by="user", relationship="mentor", tags=["thesis"])
        other = self.store.add("谭梅", by="agent", aliases=["May"], org="UCLA",
                               relationship="colleague", tags=["thesis", "ml"])
        self.store.add_account(other["id"], "wechat", "tanmei88")
        for n in range(7):
            self.store.add_activity(other["id"], f"thing {n}", f"2026-10-0{n + 1}")
        said = self.store.merge_preview(keep["id"], other["id"])
        self.assertEqual([one["handle"] for one in said["accounts"]], ["tanmei88"])
        self.assertEqual((said["activity_count"], len(said["activity"])), (7, 5))
        self.assertEqual(said["names"], ["谭梅", "May"])
        self.assertEqual(said["tags"], ["ml"])
        # Only what is empty here is filled: the relationship the user set stays.
        self.assertEqual(said["filled"], {"org": "UCLA"})
        self.assertIsNotNone(self.store.get(other["id"]), "a preview changes nothing")
        kept = self.store.merge(keep["id"], other["id"], by="user")
        self.assertEqual((kept["org"], kept["relationship"]), ("UCLA", "mentor"))
        self.assertEqual(self.store.actions("user")[0]["after"]["filled"], {"org": "UCLA"})


PNG = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"0" * 64


class PicturesTests(_Base):
    """A contact's picture from a public profile of theirs, or initials."""

    def test_where_it_is_looked_for(self) -> None:
        from personalized_agent.heartbeat.connections import avatars
        said = avatars.candidates([
            {"platform": "linkedin", "url": "https://www.linkedin.com/in/ana"},
            {"platform": "GitHub", "handle": "ana-r"},
            {"platform": "", "url": "https://github.com/anar/"}])
        self.assertEqual(said[:2], [("image", "https://github.com/ana-r.png?size=200"),
                                    ("image", "https://github.com/anar.png?size=200")])
        self.assertEqual(said[2], ("page", "https://www.linkedin.com/in/ana"))
        self.assertEqual(avatars.candidates([{"platform": "wechat", "handle": "x"}]), [])
        page = ('<meta content="/me.jpg" property="og:image">'
                '<meta name="twitter:image" content="https://cdn.x/t.png">')
        self.assertEqual(avatars.page_picture(page, "https://ana.example/about"),
                         "https://ana.example/me.jpg")
        self.assertIsNone(avatars.page_picture('<meta property="og:image" '
                                               'content="javascript:alert(1)">', "https://a/"))

    def test_found_kept_served_and_kept_through_a_merge(self) -> None:
        from personalized_agent.api.routes import build_routes
        from personalized_agent.heartbeat.connections import avatars
        ana = self.store.add("Ana", by="user")
        self.store.add_account(ana["id"], "linkedin", url="https://linkedin.com/in/ana")
        self.store.add_account(ana["id"], "github", "ana")
        asked = []

        def get(url):
            asked.append(url)
            if "github" in url:
                raise OSError("unreachable")
            if url == "https://linkedin.com/in/ana":
                return b'<meta property="og:image" content="https://img.example/a.png">',                     "text/html; charset=utf-8"
            return PNG, "image/png"

        said = avatars.fetch_avatar(self.store, ana["id"], get=get)
        self.assertTrue(said["ok"], said)
        self.assertEqual(asked, ["https://github.com/ana.png?size=200",
                                 "https://linkedin.com/in/ana", "https://img.example/a.png"])
        person = self.store.get(ana["id"])
        self.assertEqual(person["avatar_from"], "https://img.example/a.png")
        self.assertEqual(self.store.avatar_path(person).read_bytes(), PNG)
        routes = build_routes(self.app)
        served = routes[("GET", "/api/connections/avatar")](_Request({"id": ana["id"]}))
        self.assertEqual((served.body, served.content_type), (PNG, "image/png"))
        # Looked for once a day, not again at once.
        self.assertEqual(avatars.wanting(self.store), [])
        # Kept by the one a merge keeps, when it had none.
        other = self.store.add("Ana Ruiz", by="user")
        kept = self.store.merge(other["id"], ana["id"])
        self.assertTrue(self.store.avatar_path(kept))

    def test_not_an_image_is_not_kept(self) -> None:
        from personalized_agent.heartbeat.connections import avatars
        bo = self.store.add("Bo", by="user")
        self.store.add_account(bo["id"], "github", "bo")
        said = avatars.fetch_avatar(self.store, bo["id"],
                                    get=lambda url: (b"<html>", "text/html"))
        self.assertFalse(said["ok"])
        self.assertIn("not an image", said["error"])
        self.assertIsNone(self.store.avatar_path(self.store.get(bo["id"])))


class MovingAnAccountTests(_Base):

    def test_an_account_is_moved_to_the_person_it_belongs_to(self) -> None:
        from personalized_agent.api.routes import build_routes
        wrong = self.store.add("Mei Tan", by="agent")
        right = self.store.add("Wei Zhang", by="user")
        account = self.store.add_account(wrong["id"], "github", "weiz")
        routes = build_routes(self.app)
        moved = routes[("POST", "/api/connections/person")](_Request({
            "action": "move_account", "id": right["id"], "account": account["id"],
            "reason": "that GitHub is Wei Zhang's"}))
        self.assertEqual([one["handle"] for one in moved["accounts"]], ["weiz"])
        self.assertEqual(self.store.accounts(wrong["id"]), [])
        lesson = self.store.actions("user")[0]
        self.assertEqual(lesson["action"], "move an account")
        self.assertEqual((lesson["before"]["from"], lesson["after"]["to"]),
                         ("Mei Tan", "Wei Zhang"))
        self.assertIn("the account GitHub weiz moved from Mei Tan to Wei Zhang",
                      self.store.changes_text())
        self.assertIn("that GitHub is Wei Zhang's", self.store.changes_text())
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            routes[("POST", "/api/connections/person")](_Request({
                "action": "move_account", "id": right["id"], "account": "cacc_none"}))


class TheAgentFillsInTheirPublicProfilesTests(_Base):
    """A contact's public profiles: the agent may fill them in from the web -
    the user's own go to their inbox, and one the user took off stays off."""

    def test_a_profile_found_is_added_once_as_the_agents(self) -> None:
        ana = self.store.add("Ana Ruiz", by="user", org="Acme")
        self.assertTrue(self.store.found_profile(
            ana["id"], "https://www.linkedin.com/in/ana-ruiz", why="links to her Acme page"))
        self.assertFalse(self.store.found_profile(
            ana["id"], "https://linkedin.com/in/ana-ruiz/"), "once")
        self.assertFalse(self.store.found_profile(ana["id"], "not a link"))
        accounts = self.store.accounts(ana["id"])
        self.assertEqual([(one["platform"], one["source"]) for one in accounts],
                         [("LinkedIn", "found on the public web")])
        # Recorded as the agent's: not one of the user's lessons.
        self.assertEqual([one["action"] for one in self.store.actions("agent")],
                         ["add a profile"])
        self.assertNotIn("add a profile", [one["action"] for one in self.store.actions("user")])

    def test_one_the_user_took_off_is_not_added_back(self) -> None:
        ana = self.store.add("Ana Ruiz", by="user")
        account = self.store.add_account(ana["id"], "GitHub", "", "https://github.com/ana")
        self.store.remove_profile(ana["id"], account["id"], by="user", reason="not hers")
        self.assertFalse(self.store.found_profile(ana["id"], "https://github.com/ana"))
        self.assertEqual(self.store.accounts(ana["id"]), [])

    def test_not_the_users_own(self) -> None:
        me = self.store.me()
        self.assertFalse(self.store.found_profile(me["id"], "https://github.com/me-myself"))

    def test_the_round_writes_them_with_its_findings(self) -> None:
        ana = self.store.add("Ana Ruiz", by="user")
        said = self.app.connections_round._tool(
            "record_findings",
            {"id": ana["id"], "profiles": [{"url": "https://github.com/ana-r",
                                           "why": "her repositories name Acme"}]}, {})
        self.assertEqual(said.get("profiles_added"), 1, said)
        self.assertEqual([one["url"] for one in self.store.accounts(ana["id"])],
                         ["https://github.com/ana-r"])


class StepTwoTests(_Base):
    """The agent heartbeat's step 2: the people in a window."""

    def test_new_and_known_people_and_their_activity(self) -> None:
        known = self.store.add("Ana Ruiz", by="user", relationship="colleague")
        self.store.add_account(known["id"], "linkedin", url="https://www.linkedin.com/in/anaruiz/")
        said = people_step.apply(self.store, [
            {"person_id": None, "name": "Ana R.",
             "accounts": [{"platform": "linkedin", "url": "https://linkedin.com/in/anaruiz"}],
             "relationship": "friend", "org": "Acme",
             "activity": [{"at": "2026-10-11T09:00:00+01:00", "what": "Shared the Q4 plan",
                           "evidence": ["event_1"]}]},
            {"name": "Bo Chen", "relationship": "recruiter",
             "activity": [{"at": "2026-10-11T10:00:00+01:00", "what": "Asked for a call"}]}])
        self.assertTrue(all(one["ok"] for one in said), said)
        ana = self.store.get(known["id"])
        # On a contact, what the agent saw may replace what the user set.
        self.assertEqual(ana["relationship"], "friend")
        self.assertIn("colleague", ana["relationships"])
        self.assertEqual(ana["org"], "Acme")
        self.assertIn("Ana R.", ana["aliases"])
        self.assertEqual(self.store.activity(known["id"])[0]["what"], "Shared the Q4 plan")
        bo = self.store.find("Bo Chen")
        self.assertEqual((bo["relationship"], bo["added_by"]), ("recruiter", "agent"))
        # Once: the same thing on the same day is not added again.
        people_step.apply(self.store, [{"person_id": known["id"], "name": "Ana Ruiz",
                                        "activity": [{"at": "2026-10-11T18:00:00+01:00",
                                                      "what": "Shared the Q4 plan"}]}])
        self.assertEqual(len(self.store.activity(known["id"])), 1)

    def test_one_the_user_deleted_comes_back_only_with_strong_newer_evidence(self) -> None:
        gone = self.store.add("Old Friend", by="user")
        self.store.delete(gone["id"], by="user", reason="we lost touch")
        refused = people_step.apply(self.store, [{"person_id": gone["id"], "name": "Old Friend",
                                                  "activity": [{"what": "liked a post"}]}])
        self.assertFalse(refused[0]["ok"])
        self.assertTrue(self.store.get(gone["id"])["deleted"])
        back = people_step.apply(self.store, [{
            "name": "Old Friend", "add_back_because": "They are co-authoring the user's "
            "paper now: three emails this week and a meeting set for Friday.",
            "activity": [{"what": "Set a meeting for Friday"}]}])
        self.assertTrue(back[0]["ok"], back)
        self.assertFalse(self.store.get(gone["id"])["deleted"])
        action = self.store.actions("agent")[0]
        self.assertEqual(action["action"], "add back")
        self.assertIn("co-authoring", action["reason"])

    def test_its_context_lists_the_known_the_deleted_and_the_lessons(self) -> None:
        self.store.add("Ana Ruiz", by="user", relationship="colleague", org="Acme")
        gone = self.store.add("Spam Bot", by="agent")
        self.store.delete(gone["id"], by="user", reason="a newsletter, not a person")
        said = people_step.context(self.store, "an email from Ana Ruiz about the plan, "
                                               "and another newsletter from Spam Bot")
        self.assertIn("Ana Ruiz · relationships: colleague · organisations: Acme · "
                      "added by the user", said)
        self.assertTrue(said.index("Ana Ruiz") < said.index("LESSONS ABOUT THE USER'S CONTACTS"))
        self.assertIn("DELETED by the user on", said)
        self.assertIn("a newsletter, not a person", said)

    def test_a_name_in_the_news_is_not_a_contact(self) -> None:
        """What counts is the context: someone the user deals with - not a
        name they read about, nor the author of what they read."""
        said = " ".join(people_step.INSTRUCTION.split())
        for rule in ("A NAME ON THE SCREEN IS NOT A CONTACT", "DEALS WITH THEM",
                     "a name in the news", "the author of a paper, a post or a book",
                     "When in doubt, leave them out"):
            self.assertIn(rule, said)
        self.assertNotIn("the author of a post they read. Not", said)

    def test_it_is_step_two_of_the_heartbeat(self) -> None:
        from personalized_agent.heartbeat.checklist.store import STEPS
        from personalized_agent.heartbeat.core.heartbeat import (GATHERS_FOR_ITSELF,
                                                                 PAUSED_STEPS, STEP_PROMPTS)
        # What runs: who the user is stays kept, and paused.
        self.assertEqual([one.id for one in STEPS if one.id not in PAUSED_STEPS],
                         ["step_state", "step_people", "step_notify"])
        self.assertIn("step_people", GATHERS_FOR_ITSELF)
        self.assertIn("FIND THE PEOPLE IN THIS WINDOW", STEP_PROMPTS["step_people"]["instruction"])
        self.assertIn("add_back_because", STEP_PROMPTS["step_people"]["returns"])

    def test_a_whole_review_writes_the_people(self) -> None:
        from datetime import timedelta
        from personalized_agent.common.events.models import ListenerEvent, utcnow
        self.store.add("Ana Ruiz", by="user", relationship="colleague")
        self.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="screen", timestamp=utcnow() - timedelta(minutes=3),
            application="chrome.exe", data={"text": "Email from Ana Ruiz: the Q4 plan"}))
        prompts = []

        class _Models:
            def invoke(self, request, *a, **k):
                prompts.append(json.dumps([m.get("content") for m in request.messages],
                                          ensure_ascii=False, default=str))
                return SimpleNamespace(text=json.dumps({
                    "states": [], "notifications": [],
                    "people": [{"name": "Ana Ruiz", "activity": [
                        {"at": "2026-10-11T09:00:00+01:00", "what": "Sent the Q4 plan"}]}]}),
                    tool_calls=[], usage=None, model="m", provider="p", raw_content="")

            def resolve(self, *a, **k):
                return SimpleNamespace(to_dict=lambda: {})

            def check_availability(self, *a, **k):
                return SimpleNamespace(available=True, to_dict=lambda: {}, state=None)

        self.app.heartbeat.models = _Models()
        report = self.app.heartbeat.run_once(trigger="manual_debug")
        self.assertEqual(report["people_written"][0]["name"], "Ana Ruiz", report.get("error"))
        self.assertTrue(any("THE USER'S CONTACTS" in one and "Ana Ruiz" in one
                            for one in prompts))
        ana = self.store.find("Ana Ruiz")
        self.assertEqual(self.store.activity(ana["id"])[0]["what"], "Sent the Q4 plan")


class _Searcher:
    """The model of a round: works out what the user would want to know,
    then - two searches at once - searches the contacts, records and
    answers, and searches the web and answers. Its tools are called as the
    API's are (tool calls in the response). Each search takes a moment, so
    that the two can be seen to overlap (`spans`)."""

    def __init__(self, person_id, pause=0.0):
        import threading
        self.person_id, self.pause = person_id, pause
        self.asked = []
        self.spans = {"contacts": [], "web": []}
        self._lock = threading.Lock()

    def invoke(self, request, role=""):
        import time
        with self._lock:
            self.asked.append(request)
        first = json.dumps(request.messages[0], ensure_ascii=False)
        last = request.messages[-1]["content"]
        if "THE CHANGES TO FOLD IN" in json.dumps(request.messages, ensure_ascii=False):
            return ModelResponse(text="The user removed bots early on.",
                                 usage=ModelUsage(10, 5))
        if "WHAT THE USER WOULD WANT TO READ NOW" in first:
            return ModelResponse(text=json.dumps({"needs": [
                {"need": "who decides the Q4 budget", "for": "Q4 budget", "look": "both",
                 "search": "Q4 budget owner"},
                {"need": "aquariums worth a visit", "for": "the aquarium videos",
                 "look": "web", "search": "best aquariums in Asia 2026"}]}),
                usage=ModelUsage(10, 10))
        turn = sum(1 for one in request.messages if one["role"] == "assistant")
        started = time.monotonic()
        time.sleep(self.pause)
        if "Search the web for it" in first:
            self.spans["web"].append((started, time.monotonic()))
            if turn == 0:
                return ModelResponse(tool_calls=[ToolCall(
                    id="w", name="web_search", arguments={"query": "Osaka aquarium 2026"})],
                    raw_content="web", usage=ModelUsage(50, 5))
            return ModelResponse(text=json.dumps({"items": [
                {"title": "Kaiyukan reopens its whale shark tank", "detail": "From 1 Nov.",
                 "for": "the aquarium videos", "importance": 2, "why": "they were watching",
                 "sources": ["https://kaiyukan.example/news"]}]}), usage=ModelUsage(50, 10))
        self.spans["contacts"].append((started, time.monotonic()))
        if turn == 0:
            return ModelResponse(tool_calls=[
                ToolCall(id="a", name="search_people", arguments={"query": "budget"}),
                ToolCall(id="b", name="web_search", arguments={"query": "Ana Ruiz Acme"})],
                raw_content="calls", usage=ModelUsage(100, 10))
        if turn == 1:
            return ModelResponse(tool_calls=[ToolCall(id="c", name="record_findings", arguments={
                "id": self.person_id, "role": "VP Finance",
                "activity": [{"at": "2026-10-05", "what": "Promoted to VP Finance",
                              "url": "https://acme.example/news"}]})],
                raw_content="record", usage=ModelUsage(100, 10))
        assert isinstance(last, list)
        return ModelResponse(text=json.dumps({
            "items": [{"title": "Ana Ruiz is now VP Finance", "detail": "She signs off Q4.",
                       "people": [self.person_id, "person_unknown"], "kind": "person",
                       "for": "Q4 budget", "importance": 4, "why": "the budget waits on her",
                       "sources": ["https://acme.example/news"]},
                      {"title": "x", "importance": 9}]}), usage=ModelUsage(100, 20))



class _Updater(_Searcher):
    """The same searches; and, given the list as it stands, only changes."""

    def __init__(self, person_id):
        super().__init__(person_id)
        self.update_asked = None

    def invoke(self, request, role=""):
        first = json.dumps(request.messages[0], ensure_ascii=False)
        if "Update the list." in first:
            self.update_asked = request
            current = json.loads(request.messages[0]["content"].split(
                "each item with its id:\n", 1)[1].split("\n\nWHAT THIS ROUND FOUND", 1)[0])
            ids = {one["title"]: one["id"] for one in current}
            return ModelResponse(text=json.dumps({"changes": [
                {"op": "edit", "id": ids["Ana Ruiz is now VP Finance"],
                 "item": {"detail": "She signs off Q4 - by 20 October."}, "why": "a date"},
                {"op": "delete", "id": ids["Kaiyukan reopens its whale shark tank"],
                 "why": "they stopped watching aquarium videos"},
                {"op": "delete", "id": ids["x"]},
                {"op": "add", "item": {"title": "Budget review on Friday", "importance": 3,
                                       "kind": "across"}}]}), usage=ModelUsage(10, 10))
        return super().invoke(request, role)

class TheRoundTests(_Base):

    def _a_state(self, title, loose=False, hours_ago=1):
        from datetime import datetime, timedelta, timezone
        at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        ident = f"state_{abs(hash(title)) % 10**12}"
        self.app.db.execute(
            "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
            "created_at, updated_at) VALUES (?, ?, '', 'agent', '', ?, '', 'active', ?, ?)",
            (ident, title, at, at, at))
        if loose:
            self.app.db.execute("INSERT INTO state_task_loose (state_id, scope, since) "
                                "VALUES (?, 'live', ?)", (ident, at))

    def test_each_issue_is_information_found_fresh(self) -> None:
        """No list is carried from issue to issue: each issue's stories are
        found for it - news to read, not who is after the user - and both
        searches are told what the last issues carried, not to find it again."""
        ana = self.store.add("Ana Ruiz", by="user", relationship="colleague", closeness=4,
                             org="Acme", tags=["budget"])
        self._a_state("The user drafted the Q4 budget")
        search = lambda q: {"results": []}
        fetch = lambda url: {"text": ""}
        ConnectionsRound(self.store, _Searcher(ana["id"]), self.app.db, search=search,
                         fetch=fetch).run()
        first = [one["title"] for one in self.store.briefing()["items"]]
        self.assertTrue(first)
        self.assertTrue(all(one.get("recommended") for one in self.store.briefing()["items"]))
        models = _Searcher(ana["id"])
        ConnectionsRound(self.store, models, self.app.db, search=search, fetch=fetch).run()
        searches = [one for one in models.asked if "Search the web for it" in
                    json.dumps(one.messages[0], ensure_ascii=False) or "DO THIS - a search"
                    in json.dumps(one.messages[0], ensure_ascii=False)]
        self.assertTrue(searches)
        for one in searches:
            content = one.messages[0]["content"]
            self.assertIn("WHAT THE LAST ISSUES CARRIED", content)
            self.assertIn(first[0], content)
            # Information, not who is after the user.
            self.assertNotIn("who is waiting on them, news that", content)
        self.assertEqual(self.store.briefing()["stats"]["changes"], [])
        # Never the list's update: no model was asked to update a list.
        self.assertFalse([one for one in models.asked if "Update the list." in
                          json.dumps(one.messages[0], ensure_ascii=False)])

    def test_with_connections_off_an_issue_is_the_news_alone(self) -> None:
        """The user, 2026-10-30: a classmate turned Connections off and the
        issue still looked her contacts up. Now no one is looked up and
        every call is the Newspaper's; on, the contacts' calls are named
        Connections'."""
        ana = self.store.add("Ana Ruiz", by="user", relationship="colleague", closeness=4,
                             org="Acme", tags=["budget"])
        self._a_state("The user drafted the Q4 budget")
        searched = []
        models = _Searcher(ana["id"])
        round_ = ConnectionsRound(self.store, models, self.app.db,
                                  search=lambda q: searched.append(q) or {"results": []},
                                  fetch=lambda url: {"text": ""})
        round_.people_on = lambda: False
        round_.run()
        self.assertEqual(searched, ["Osaka aquarium 2026"], "no contact looked up")
        self.assertFalse(self.store.get(ana["id"], with_details=True)["role"])
        self.assertTrue(models.asked)
        self.assertEqual({one.task for one in models.asked}, {"newspaper"})
        self.assertTrue(self.store.briefing()["items"], "the news still comes")
        # On: the contacts' calls are Connections', the news' the Newspaper's.
        models = _Searcher(ana["id"])
        ConnectionsRound(self.store, models, self.app.db, search=lambda q: {"results": []},
                         fetch=lambda url: {"text": ""}).run()
        tasks = {one.task for one in models.asked}
        self.assertEqual(tasks, {"connections", "newspaper"})
        searching = [one for one in models.asked if "Search the web for it" in
                     json.dumps(one.messages[0], ensure_ascii=False)]
        self.assertTrue(searching)
        self.assertTrue(all(one.task == "newspaper" for one in searching))

    def test_what_the_work_needs_to_know_is_found_written_and_ranked(self) -> None:
        ana = self.store.add("Ana Ruiz", by="user", relationship="colleague", closeness=4,
                             org="Acme", tags=["budget"])
        self._a_state("The user is watching a YouTube video about Asian aquariums", loose=True)
        self._a_state("The user drafted the Q4 budget")
        self._a_state("Something from last week", hours_ago=24 * 7)
        searched = []
        models = _Searcher(ana["id"], pause=0.3)
        round_ = ConnectionsRound(self.store, models, self.app.db,
                                  search=lambda q: searched.append(q) or {"results": []},
                                  fetch=lambda url: {"text": ""})
        said = round_.run()
        # Both searches: the contacts' and the web's.
        self.assertEqual(sorted(searched), ["Ana Ruiz Acme", "Osaka aquarium 2026"])
        self.assertEqual(said["used"]["web"], 2)
        # ... AT ONCE: the web's began before the contacts' ended.
        contacts_end = max(end for _start, end in models.spans["contacts"])
        web_start = min(start for start, _end in models.spans["web"])
        self.assertLess(web_start, contacts_end, models.spans)
        # Found, written to her, dated, from the web - and checked.
        ana = self.store.get(ana["id"], with_details=True)
        self.assertEqual(ana["role"], "VP Finance")
        self.assertEqual(ana["activity"][0]["source"], "the public web")
        self.assertTrue(ana["checked_at"])
        # Ranked for the Overview, the unknown person dropped, bounds kept:
        # the people's and the web's together, the most important first.
        briefing = self.store.briefing()
        self.assertEqual([(one["title"], one["importance"], one["kind"])
                          for one in briefing["items"]],
                         [("x", 5, "person"), ("Ana Ruiz is now VP Finance", 4, "person"),
                          ("Kaiyukan reopens its whale shark tank", 2, "web")])
        found = briefing["items"][1]
        self.assertEqual(found["people"], [ana["id"]])
        self.assertEqual(found["names"], ["Ana Ruiz"])
        self.assertEqual(briefing["items"][2]["people"], [])
        self.assertEqual([one["need"] for one in briefing["needs"]],
                         ["who decides the Q4 budget", "aquariums worth a visit"])
        # What it started from: the user's high-level tasks, as the state
        # heartbeat summarised them - never what the agent heartbeat saw
        # moment to moment.
        asked = [json.dumps(one.messages[0], ensure_ascii=False) for one in models.asked]
        needs_prompt = next(one for one in asked if "WOULD WANT TO READ NOW" in one)
        self.assertIn("every one of their current high-level tasks", needs_prompt)
        self.assertNotIn("YouTube", needs_prompt)
        self.assertNotIn("The user drafted the Q4 budget", needs_prompt)
        contacts_prompt = next(one for one in asked if "a search of their CONTACTS" in one)
        self.assertIn(f"[{ana['id']}] Ana Ruiz", contacts_prompt)
        self.assertIn("who decides the Q4 budget", contacts_prompt)
        web_prompt = next(one for one in asked if "Search the web for it" in one)
        self.assertIn("aquariums worth a visit", web_prompt)
        # Never the calendar.
        self.assertEqual(self.app.scheduler.store.list(), [])

    def test_a_round_summarises_nothing_the_lessons_engine_learns_instead(self) -> None:
        """With the lessons engine, every change is its evidence: the round
        no longer folds the older ones into a summary."""
        person = self.store.add("P", by="user")
        for n in range(KEEP_ACTIONS + 2):
            self.store.edit(person["id"], by="user", summary=f"n{n}")
        models = _Searcher(person["id"])
        round_ = ConnectionsRound(self.store, models, self.app.db,
                                  search=lambda q: {}, fetch=lambda u: {})
        round_.run()
        self.assertEqual(self.store.lessons_summary()["text"], "")
        self.assertFalse([one for one in models.asked if "THE CHANGES TO FOLD IN"
                          in json.dumps(one.messages, ensure_ascii=False)])
        evidence = self.app.db.query("SELECT * FROM lesson_evidence WHERE kind = 'connections'")
        self.assertEqual(len(evidence), KEEP_ACTIONS + 3)

    def test_it_searches_until_the_model_stops_only_a_runaway_is_ended(self) -> None:
        from personalized_agent.heartbeat.connections import round as round_module
        round_ = ConnectionsRound(self.store, None, self.app.db, search=lambda q: {"ok": q},
                                  fetch=lambda u: {})
        used = {"calls": 0, "web": 50, "tokens_in": 10**9, "tokens_out": 10**8}
        self.assertEqual(round_._tool("web_search", {"query": "x"}, used), {"ok": "x"})
        self.assertTrue(round_._room(used))
        used["calls"] = round_module.RUNAWAY_CALLS
        self.assertFalse(round_._room(used))
        used["web"] = round_module.RUNAWAY_WEB
        self.assertIn("answer with what you found",
                      round_._tool("web_search", {"query": "x"}, used)["error"])

    def test_work_context_reads_the_state_heartbeats_tasks(self) -> None:
        self.app.db.execute("INSERT INTO state_projects (id, scope, name, why, created_at, "
                            "updated_at) VALUES ('p1', 'live', 'Q4 budget', 'plan the spend', "
                            "'2026-10-01', '2026-10-02')")
        said = work_context(self.app.db)
        # A project is a top-level task - their aggregate - and said as one.
        self.assertIn("TASK: Q4 budget", said)
        self.assertIn("plan the spend", said)

    def test_it_runs_at_its_own_times_not_after_a_state_round(self) -> None:
        # Newspaper is made at the times of day the user chose
        # (schedule.py) - no longer a step of the state heartbeat.
        state = self.app.state_heartbeat
        record = {"done": True, "reads_until": "2026-10-11T10:00:00+00:00"}
        from unittest import mock
        with mock.patch.object(type(state), "_round_record", return_value=record),                 mock.patch.object(type(state), "_round_in_progress", return_value=False):
            self.assertNotIn("connections", state._steps_left())
        with mock.patch.object(self.app.connections_round, "start",
                               return_value=True) as start:
            from datetime import datetime, timedelta
            self.app.config.set("recommend_times", "08:00", internal=True)
            now = datetime.now().astimezone().replace(hour=9, minute=0)
            # Its switch off (as it is at first): nothing by itself.
            self.assertFalse(self.app.issue_clock.look(now))
            self.assertEqual(start.call_count, 0)
            self.app.config.set("recommend_auto_on", True, internal=True)
            self.assertTrue(self.app.issue_clock.look(now))
            self.assertEqual(start.call_count, 1)
        # Its bar is still shown on the State heartbeat's panel.
        self.assertIn("connections", state.status())


class TheRoutesTests(_Base):

    def setUp(self) -> None:
        super().setUp()
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)

    def _post(self, path, body):
        return self.routes[("POST", path)](_Request(body))

    def _get(self, path, body=None):
        return self.routes[("GET", path)](_Request(body or {}))

    def test_several_jobs_and_places_through_the_panel(self) -> None:
        made = self._post("/api/connections/person", {"action": "add", "name": "Bo",
                                                       "profession": "teacher"})
        self._post("/api/connections/person", {
            "action": "edit", "id": made["id"], "professions": ["writer", "teacher"],
            "locations": ["Boston", "Lisbon"]})
        whole = self._get("/api/connections/person", {"id": made["id"]})
        self.assertEqual((whole["professions"], whole["profession"]),
                         (["writer", "teacher"], "writer"))
        self.assertEqual((whole["locations"], whole["location"]),
                         (["Boston", "Lisbon"], "Boston"))

    def test_the_users_changes_through_the_panel(self) -> None:
        from personalized_agent.api.errors import ApiError
        made = self._post("/api/connections/person", {"action": "add", "name": "Ana",
                                                       "relationship": "friend",
                                                       "reason": "from the climbing gym"})
        self._post("/api/connections/person", {"action": "edit", "id": made["id"],
                                               "org": "Acme", "reason": "new job"})
        listed = self._get("/api/connections")
        self.assertEqual([one["name"] for one in listed["people"]], ["Ana"])
        self.assertEqual(listed["lessons"]["kept"], 2)
        self.assertIn("friend", listed["relationships"])
        whole = self._get("/api/connections/person", {"id": made["id"]})
        self.assertEqual(whole["actions"][0]["reason"], "new job")
        self._post("/api/connections/person", {"action": "delete", "id": made["id"],
                                               "reason": "moved away"})
        lessons = self._get("/api/connections/lessons")
        self.assertEqual(lessons["deleted"][0]["deleted_reason"], "moved away")
        self._post("/api/connections/person", {"action": "restore", "id": made["id"]})
        self.assertEqual(len(self._get("/api/connections")["people"]), 1)
        light = self._get("/api/connections/briefing")
        self.assertEqual(set(light), {"briefing", "progress", "issues", "schedule", "says",
                                      # the stories kept, across issues
                                      "collection", "collected"})
        # A profile of the user's own: on the one profile that is theirs, never
        # among the contacts.
        self._post("/api/connections/source", {"url": "https://github.com/me-myself"})
        listed = self._get("/api/connections")
        self.assertEqual([one["url"] for one in listed["me"]["accounts"]],
                         ["https://github.com/me-myself"])
        self.assertEqual([one["name"] for one in listed["people"]], ["Ana"])
        with self.assertRaises(ApiError):
            self._post("/api/connections/source", {"url": "file:///etc/passwd"})
        with self.assertRaises(ApiError):
            self._post("/api/connections/person", {"action": "edit", "id": "person_none"})


def _route(app, method, path, body):
    from personalized_agent.api.routes import build_routes
    return build_routes(app)[(method, path)](_Request(body))


class _MeLookedUp:
    """The model of the user's own step: reads their page, records what they
    did, suggests a change to their details - and of the contacts' step,
    answers at once."""

    def __init__(self, me_id, contact_id=""):
        self.me_id, self.contact_id = me_id, contact_id
        self.asked = []

    def invoke(self, request, role=""):
        self.asked.append(request)
        if "THE USER, as known" not in json.dumps(request.messages[0], ensure_ascii=False):
            if self.contact_id and len(request.messages) == 1:
                # The contacts' step, trying to write to the user: refused.
                return ModelResponse(tool_calls=[ToolCall(id="z", name="record_findings",
                    arguments={"id": self.me_id, "role": "Nope"})], raw_content="x",
                    usage=ModelUsage(1, 1))
            return ModelResponse(text='{"needs": [], "items": []}', usage=ModelUsage(1, 1))
        turn = sum(1 for one in request.messages if one["role"] == "assistant")
        if turn == 0:
            return ModelResponse(tool_calls=[
                ToolCall(id="a", name="fetch_page", arguments={"url": "https://me.example"})],
                raw_content="read", usage=ModelUsage(10, 1))
        if turn == 1:
            return ModelResponse(tool_calls=[
                ToolCall(id="b", name="record_my_activity", arguments={"activity": [
                    {"at": "2026-10-01", "what": "Gave a talk at PyCon",
                     "url": "https://me.example/talks"},
                    {"at": "2026-10-02", "what": "Something with no page"}]}),
                ToolCall(id="c", name="propose_my_profile", arguments={"changes": [
                    {"field": "role", "value": "Research Scientist", "why": "the page says so",
                     "source": "https://me.example"},
                    {"field": "org", "value": "", "why": "", "source": "x"}]})],
                raw_content="write", usage=ModelUsage(10, 1))
        return ModelResponse(text="Found a talk and a new role.", usage=ModelUsage(10, 1))


class YourOwnProfileTests(_Base):

    def test_you_are_one_profile_never_among_the_contacts(self) -> None:
        me = self.store.me()
        self.assertTrue(me["is_self"])
        self.assertEqual(self.store.me()["id"], me["id"], "only one is yours")
        ana = self.store.add("Ana", by="user")
        self.assertEqual([one["id"] for one in self.store.people()], [ana["id"]])
        self.assertNotIn(me["id"], [one["id"] for one in self.store.search("")])
        # Step 2 never writes the user from their screen - not even by an
        # account of theirs it saw.
        self.store.add_profile(me["id"], "https://github.com/me-myself")
        said = people_step.apply(self.store, [{
            "name": "Someone", "accounts": [{"url": "https://github.com/me-myself"}],
            "activity": [{"at": "2026-10-11", "what": "Typed an email"}]}])
        self.assertEqual(said[0]["error"], "the user themselves")
        self.assertEqual(self.store.me()["activity"], [])

    def test_a_second_you_is_folded_into_the_first(self) -> None:
        me = self.store.me()
        self.store.add_profile(me["id"], "https://me.example")
        self.app.db.execute(
            "INSERT INTO connection_people (id, name, org, created_at, updated_at, is_self) "
            "VALUES ('person_other_me', 'Robin H', 'Northfield University', '2099-01-01', '2099-01-01', 1)")
        self.store.add_account("person_other_me", "GitHub", "", "https://github.com/me2")
        one = self.store.me()
        self.assertEqual(one["id"], me["id"])
        self.assertEqual((one["name"], one["org"]), ("Robin H", "Northfield University"))
        self.assertEqual(sorted(a["url"] for a in one["accounts"]),
                         ["https://github.com/me2", "https://me.example"])
        self.assertIsNone(self.store.get("person_other_me"))

    def test_several_profiles_when_adding_and_each_one_a_lesson(self) -> None:
        made = self._post("/api/connections/person", {
            "action": "add", "name": "Bo", "profiles": [
                {"url": "https://github.com/bo"}, "https://www.linkedin.com/in/bo", ""]})
        self.assertEqual(sorted(one["platform"] for one in made["accounts"]),
                         ["GitHub", "LinkedIn"])
        gone = made["accounts"][0]
        after = self._post("/api/connections/person", {
            "action": "remove_profile", "id": made["id"], "account": gone["id"],
            "reason": "not his"})
        self.assertEqual(len(after["accounts"]), 1)
        kinds = [one["action"] for one in self.store.actions("user", 10)]
        self.assertIn("add a profile", kinds)
        self.assertIn("remove a profile", kinds)
        self.assertIn("not his", self.store.changes_text())

    def _post(self, path, body):
        return _route(self.app, "POST", path, body)

    def test_your_own_step_records_what_you_did_and_asks_about_your_details(self) -> None:
        me = self.store.me()
        self.store.add_profile(me["id"], "https://me.example")
        ana = self.store.add("Ana", by="user", closeness=5)
        models = _MeLookedUp(me["id"], ana["id"])
        fetched = []
        round_ = ConnectionsRound(self.store, models, self.app.db,
                                  search=lambda q: {"results": []},
                                  fetch=lambda url: fetched.append(url) or {"text": "hi"})
        said = round_.run()
        self.assertEqual(said["me"]["activity"], 1)
        self.assertEqual(said["me"]["asked"], 1)
        self.assertEqual(fetched, ["https://me.example"])
        me = self.store.me()
        # What you did in public: recorded at once, only with its page.
        self.assertEqual([(one["what"], one["source"]) for one in me["activity"]],
                         [("Gave a talk at PyCon", "your public page")])
        self.assertTrue(me["activity"][0]["found_at"] and me["activity"][0]["created_at"])
        # Your details: NOT changed - waiting in your inbox.
        self.assertEqual(me["role"], "")
        inbox = self.store.inbox()
        self.assertEqual([one["change"] for one in inbox],
                         [{"field": "role", "value": "Research Scientist", "before": ""}])
        self.assertEqual(inbox[0]["sources"], ["https://me.example"])
        # The contacts' step could not write to you.
        self.assertNotEqual(self.store.me()["role"], "Nope")
        # Looked up lately: not again, unless asked.
        self.assertEqual(round_._look_me_up(dict(calls=0, web=0, tokens_in=0, tokens_out=0),
                                            lambda: True), {"skipped": "looked up lately"})

    def test_no_profile_of_yours_no_looking_up(self) -> None:
        round_ = ConnectionsRound(self.store, None, self.app.db, search=lambda q: {},
                                  fetch=lambda u: {})
        self.assertEqual(round_._look_me_up(dict(calls=0, web=0, tokens_in=0, tokens_out=0),
                                            lambda: True),
                         {"skipped": "no public profile of yours given"})

    def test_your_own_profile_is_myself_and_no_lesson(self) -> None:
        """Changes to the user's own profile - found by the agent and checked
        by them, or made by them - are kept, never lessons; and who they are
        to themselves is "myself", whoever tries to change it."""
        me = self.store.me()
        self.assertEqual((me["relationship"], me["relationships"]), ("myself", ["myself"]))
        asked = self.store.propose_for_me({"field": "name", "value": "Robin Hale"}, "the page")
        self.store.decide(asked["id"], True)
        self.store.edit(me["id"], by="user", reason="my role", role="PhD student",
                        relationship="friend")
        self.store.edit(me["id"], by="agent", relationships=["colleague"])
        me = self.store.me()
        self.assertEqual((me["name"], me["role"]), ("Robin Hale", "PhD student"))
        self.assertEqual(me["relationships"], ["myself"])
        mei = self.store.add("Mei Tan", by="user", reason="my advisor")
        self.assertEqual([one["person_id"] for one in self.store.actions("user")],
                         [mei["id"]])
        self.assertEqual([one["title"] for one in self.store.describe_lessons()["lessons"]],
                         ["Mei Tan"])
        self.assertNotIn("Robin Hale", self.store.changes_text())

    def test_every_issue_is_kept_numbered_the_newest_hundred(self) -> None:
        if self.store.folder is None:
            self.skipTest("no folder for pictures")
        store = self.store
        old_picture = store.save_briefing_picture(b"old", "png")
        store.save_briefing([], [{"title": "first", "image": {"kept": old_picture}}], {})
        for n in range(store.MOST_ISSUES):
            store.save_briefing([], [{"title": f"story {n}"}], {})
        issues = store.briefings()
        self.assertEqual(len(issues), store.MOST_ISSUES)
        self.assertEqual(issues[0]["issue"], store.MOST_ISSUES + 1)
        self.assertEqual(issues[-1]["issue"], 2, "the first is gone")
        self.assertEqual(store.briefing(issues[-1]["id"])["items"][0]["title"], "story 0")
        # Its picture went with the last issue that had it.
        self.assertIsNone(store.briefing_picture_path(old_picture))

    def test_the_inbox_accepted_dismissed_and_not_asked_again(self) -> None:
        me = self.store.me()
        asked = self.store.propose_for_me({"field": "name", "value": "Robin Hale"}, "the page")
        self.assertIsNotNone(asked)
        self.assertIsNone(self.store.propose_for_me({"field": "name", "value": "Robin Hale"}),
                          "the same is waiting already")
        done = self.store.decide(asked["id"], True, "yes")
        self.assertEqual(done["status"], "accepted")
        me = self.store.me()
        self.assertEqual(me["name"], "Robin Hale")
        self.assertIn("name", me["user_set"], "checked by the user: theirs")
        self.assertIsNone(self.store.propose_for_me({"field": "name", "value": "robin hale"}),
                          "already so")
        link = self.store.propose_for_me({"field": "add_profile",
                                          "url": "https://x.com/someone"}, "maybe")
        self.store.decide(link["id"], False, "not mine")
        self.assertEqual(self.store.me()["accounts"], [])
        self.assertIsNone(self.store.propose_for_me({"field": "add_profile",
                                                     "url": "https://x.com/someone"}),
                          "dismissed: not asked again")
        with self.assertRaises(ValueError):
            self.store.decide(link["id"], True)
        with self.assertRaises(ValueError):
            self.store.propose_for_me({"field": "summary", "value": "x"})

    def test_pages_given_before_are_not_made_yours_without_asking(self) -> None:
        ana = self.store.add("Ana", by="user")
        self.app.db.execute("INSERT INTO connection_sources (id, url, label, person_id, "
                            "updated_at) VALUES ('s1', 'https://ana.example', '', ?, "
                            "'2026-01-02T00:00:00')", (ana["id"],))
        self.app.db.execute("INSERT INTO connection_sources (id, url, label, person_id, "
                            "updated_at) VALUES ('s2', 'https://who.example', 'a page', '', "
                            "'2026-01-03T00:00:00')")
        self.store.ensure()
        self.assertEqual([one["url"] for one in self.store.get(ana["id"], True)["accounts"]],
                         ["https://ana.example"])
        self.assertEqual(self.store.me()["accounts"], [], "nobody's page is not yours")
        inbox = self.store.inbox()
        self.assertEqual(inbox[0]["change"]["url"], "https://who.example")
        self.assertEqual(inbox[0]["found_at"], "2026-01-03T00:00:00")
        self.assertEqual(self.store.sources(), [])


class _Tidies:
    """The model of the tidying step: says which names are one thing."""

    def __init__(self, groups):
        self.groups, self.asked = groups, []

    def invoke(self, request, role=""):
        self.asked.append(request)
        text = json.dumps(request.messages[0], ensure_ascii=False)
        if "ONE THING WRITTEN DIFFERENTLY" in text:
            return ModelResponse(text=json.dumps({"groups": self.groups}),
                                 usage=ModelUsage(5, 5))
        return ModelResponse(text='{"needs": [], "items": []}', usage=ModelUsage(1, 1))


class OneSpellingForEachThingTests(_Base):

    def test_labels_and_organisations_are_written_as_the_one_in_use(self) -> None:
        a = self.store.add("A", by="user", org="Northfield University")
        b = self.store.add("B", by="agent", org="northfield-university")
        self.assertEqual(self.store.get(b["id"])["org"], "Northfield University")
        self.store.add_account(a["id"], "twitter", "a", "")
        self.store.add_account(a["id"], "领英", "", "https://www.linkedin.com/in/a")
        self.assertEqual(sorted(one["platform"] for one in self.store.accounts(a["id"])),
                         ["LinkedIn", "X"])
        labels = [one["name"] for one in self.store.names_in_use("label")]
        self.assertEqual(labels[:2], ["LinkedIn", "X"], "in use first")
        self.assertIn("GitHub", labels, "the known platforms offered too")
        self.assertIn("Northfield University", self._get()["orgs"])
        self.assertIn("LinkedIn", self._get()["labels"])

    def _get(self):
        return _route(self.app, "GET", "/api/connections", {})

    def test_a_profile_added_with_its_label(self) -> None:
        a = self.store.add("A", by="user")
        said = _route(self.app, "POST", "/api/connections/person", {
            "action": "add_profile", "id": a["id"], "url": "https://a.example.org",
            "label": "homepage"})
        self.assertEqual(said["accounts"][0]["platform"], "Homepage")
        made = _route(self.app, "POST", "/api/connections/person", {
            "action": "add", "name": "B", "profiles": [
                {"url": "https://b.example.org", "label": "Blog"}]})
        self.assertEqual(made["accounts"][0]["platform"], "Blog")

    def test_the_round_merges_what_means_the_same_keeping_the_users_spelling(self) -> None:
        a = self.store.add("A", by="user", org="NFU")
        b = self.store.add("B", by="agent", org="Northfield University")
        c = self.store.add("C", by="agent", org="Harbor College")
        models = _Tidies([{"kind": "org", "keep": "Northfield University",
                           "same": ["NFU"]},
                          {"kind": "org", "keep": "Nowhere", "same": ["Unheard of"]}])
        round_ = ConnectionsRound(self.store, models, self.app.db, search=lambda q: {},
                                  fetch=lambda u: {})
        said = round_.run()
        # NFU was the user's: theirs is kept, for both.
        self.assertEqual([one["keep"] for one in said["tidied"]["merged"]], ["NFU"])
        self.assertEqual({self.store.get(one["id"])["org"] for one in (a, b)}, {"NFU"})
        self.assertEqual(self.store.get(c["id"])["org"], "Harbor College")
        # Learned: written so from now on.
        later = self.store.add("D", by="agent", org="Northfield University")
        self.assertEqual(later["org"], "NFU")
        # Nothing new since: not asked again.
        asked = len(models.asked)
        self.assertIn("skipped", round_._tidy_names(dict(calls=0, web=0, tokens_in=0,
                                                         tokens_out=0)))
        self.assertEqual(len(models.asked), asked)

    def test_you_cannot_be_deleted_or_merged_away(self) -> None:
        me = self.store.me()
        other = self.store.add("Other", by="user")
        with self.assertRaises(ValueError):
            self.store.delete(me["id"])
        with self.assertRaises(ValueError):
            self.store.merge(other["id"], me["id"])
        self.assertFalse(self.store.me()["deleted"])


class TimesTests(_Base):

    def test_each_record_keeps_when_it_was_found_and_written(self) -> None:
        ana = self.store.add("Ana", by="agent", org="Acme", found_at="2026-01-01T00:00:00")
        first = ana["field_times"]["org"]
        self.assertEqual(first["found_at"], "2026-01-01T00:00:00")
        # Said again later: found again; not changed.
        again = self.store.edit(ana["id"], by="agent", org="Acme",
                                found_at="2026-05-01T00:00:00")
        self.assertEqual(again["field_times"]["org"]["found_at"], "2026-05-01T00:00:00")
        self.assertEqual(again["field_times"]["org"]["updated_at"], first["updated_at"])
        # An older sighting does not move it back.
        older = self.store.edit(ana["id"], by="agent", org="Acme",
                                found_at="2026-02-01T00:00:00")
        self.assertEqual(older["field_times"]["org"]["found_at"], "2026-05-01T00:00:00")
        changed = self.store.edit(ana["id"], by="agent", org="Globex")
        self.assertGreaterEqual(changed["field_times"]["org"]["updated_at"], first["updated_at"])
        self.assertEqual(changed["field_times"]["org"]["by"], "agent")
        # An account and an activity: found, and written.
        account = self.store.add_account(ana["id"], "GitHub", "", "https://github.com/ana",
                                         found_at="2026-03-01T00:00:00")
        self.assertEqual(account["found_at"], "2026-03-01T00:00:00")
        self.store.add_account(ana["id"], "GitHub", "", "https://github.com/ana",
                               found_at="2026-06-01T00:00:00")
        self.assertEqual(self.store.accounts(ana["id"])[0]["found_at"], "2026-06-01T00:00:00")
        self.store.add_activity(ana["id"], "Moved", "", found_at="2026-04-01T00:00:00")
        one = self.store.activity(ana["id"])[0]
        self.assertEqual(one["at"], "2026-04-01T00:00:00", "undated: dated when found")
        self.assertEqual(one["found_at"], "2026-04-01T00:00:00")
        self.assertTrue(one["created_at"])
        # The model is told: the times are in what it reads.
        self.assertIn("TIMES", __import__(
            "personalized_agent.heartbeat.connections.round", fromlist=["SYSTEM"]).SYSTEM)


class ConnectionLessonsInTheLessonsTabTests(_Base):

    def test_listed_with_the_notification_lessons_and_deletable(self) -> None:
        ana = self.store.add("Ana", by="user", reason="gym")
        self.store.edit(ana["id"], by="user", org="Acme", reason="new job")
        self.store.delete(ana["id"], by="user", reason="moved away")
        kinds = _route(self.app, "GET", "/api/lessons", {})["kinds"]
        self.assertEqual([one["kind"] for one in kinds], ["notifications", "connections"])
        mine = kinds[1]
        self.assertEqual([one["did"] for one in mine["lessons"]],
                         ["deleted", "edited", "added"])
        self.assertTrue(mine["lessons"][0]["deleted_now"])
        self.assertEqual(mine["lessons"][1]["event"]["changes"],
                         [{"field": "org", "before": "", "after": "Acme"}])
        self.assertEqual(mine["lessons"][1]["event"]["user_said"], "new job")
        _route(self.app, "POST", "/api/lessons/delete",
               {"kind": "connections", "id": mine["lessons"][1]["id"]})
        self.assertNotIn("new job", self.store.changes_text())
        self.assertEqual(self.store.get(ana["id"])["org"], "Acme", "the change itself stands")


class InTheLessonsPackageTests(unittest.TestCase):

    def test_packed_and_loaded_on_another_computer(self) -> None:
        one, other = TemporaryAgent(), TemporaryAgent()
        try:
            store = one.app.connections
            ana = store.add("Ana Ruiz", by="user", relationship="colleague", reason="team")
            store.add_activity(ana["id"], "Sent the Q4 plan", "2026-10-11T09:00:00+01:00")
            store.add_source("https://github.com/ana", "Ana's GitHub")
            gone = store.add("Spam", by="agent")
            store.delete(gone["id"], by="user", reason="a bot")
            stats = one.app.lessons_package.package()
            report = other.app.lessons_package.load(stats["zip_path"])
            self.assertEqual(report["kinds"]["connection_person"]["added"], 2)
            here = other.app.connections
            loaded = here.get(ana["id"], with_details=True)
            self.assertEqual(loaded["relationship"], "colleague")
            self.assertEqual(loaded["activity"][0]["what"], "Sent the Q4 plan")
            self.assertTrue(here.get(gone["id"])["deleted"])
            self.assertIn("a bot", here.changes_text())
            self.assertEqual(here.sources()[0]["label"], "Ana's GitHub")
        finally:
            one.cleanup()
            other.cleanup()



class SeveralRelationshipsAndOrganisationsTests(_Base):
    """A person has several relationships and several organisations - each
    with their role there - the first of each the main one; and a job or
    profession. The user's lists are as they gave them; the agent adds to
    them, and moves the main one only when the user did not choose it."""

    def test_the_main_one_and_the_rest(self) -> None:
        ana = self.store.add("Ana", by="user", relationships=["colleague", "friend"],
                             affiliations=[{"org": "Acme", "role": "VP Finance"},
                                           {"org": "MIT", "role": "lecturer"}],
                             profession="accountant")
        self.assertEqual(ana["relationships"], ["colleague", "friend"])
        self.assertEqual(ana["relationship"], "colleague")
        self.assertEqual((ana["org"], ana["role"]), ("Acme", "VP Finance"))
        self.assertEqual([one["org"] for one in ana["affiliations"]], ["Acme", "MIT"])
        self.assertEqual(ana["profession"], "accountant")
        # The main one chosen again: the others kept after it.
        ana = self.store.edit(ana["id"], by="user", relationships=["friend", "colleague"],
                              affiliations=[ana["affiliations"][1], ana["affiliations"][0]])
        self.assertEqual((ana["relationship"], ana["org"], ana["role"]),
                         ("friend", "MIT", "lecturer"))
        # One relationship given alone is the main one; the rest stay.
        ana = self.store.edit(ana["id"], by="user", relationship="mentor")
        self.assertEqual(ana["relationships"], ["mentor", "colleague"])
        # Searched by any of them.
        self.assertEqual([one["id"] for one in self.store.search("lecturer")], [ana["id"]])
        self.assertEqual([one["id"] for one in self.store.search("", "colleague")], [ana["id"]])
        self.assertIn("Acme", [one["name"] for one in self.store.names_in_use("org")])
        self.assertIn("MIT", [one["name"] for one in self.store.names_in_use("org")])

    def test_on_a_contact_the_agents_find_comes_first(self) -> None:
        # Even over what the user chose: on a contact, the agent's newest
        # find is the main one, and what the user had is kept after it.
        ana = self.store.add("Ana", by="user", relationship="colleague", org="Acme")
        ana = self.store.edit(ana["id"], by="agent", relationships=["friend"],
                              affiliations=[{"org": "Globex", "role": "advisor"}],
                              role="VP Finance", profession="accountant")
        self.assertEqual(ana["relationships"], ["friend", "colleague"])
        self.assertEqual([one["org"] for one in ana["affiliations"]], ["Globex", "Acme"])
        self.assertEqual(ana["org"], "Globex")
        self.assertEqual(ana["profession"], "accountant")
        # Another organisation alone: the main one now.
        ana = self.store.edit(ana["id"], by="agent", org="Initech", role="CFO")
        self.assertEqual((ana["org"], ana["role"]), ("Initech", "CFO"))
        # One the user did not choose: the agent's first is the main one.
        bo = self.store.add("Bo", by="agent", relationship="classmate", org="Acme")
        bo = self.store.edit(bo["id"], by="agent", relationships=["colleague"],
                             affiliations=[{"org": "Globex", "role": "engineer"}])
        self.assertEqual(bo["relationships"], ["colleague", "classmate"])
        self.assertEqual((bo["org"], bo["role"]), ("Globex", "engineer"))
        self.assertEqual([one["org"] for one in bo["affiliations"]], ["Globex", "Acme"])

    def test_people_from_before_have_theirs_as_the_main_one(self) -> None:
        ana = self.store.add("Ana", by="user", relationship="friend", org="Acme", role="CFO")
        self.app.db.execute("UPDATE connection_people SET relationships = '[]', "
                            "affiliations = '[]' WHERE id = ?", (ana["id"],))
        ConnectionsStore(self.app.db)
        row = self.app.db.query_one("SELECT relationships, affiliations FROM connection_people "
                                    "WHERE id = ?", (ana["id"],))
        self.assertEqual(json.loads(row["relationships"]), ["friend"])
        self.assertEqual(json.loads(row["affiliations"]), [{"org": "Acme", "role": "CFO"}])

    def test_a_merge_brings_their_others_after_this_ones(self) -> None:
        keep = self.store.add("Ana", by="user", relationship="colleague", org="Acme")
        other = self.store.add("Ana R", by="agent", relationships=["friend"],
                               affiliations=[{"org": "MIT", "role": "lecturer"}],
                               profession="accountant")
        said = self.store.merge_preview(keep["id"], other["id"])
        self.assertEqual(said["relationships"], ["friend"])
        self.assertEqual(said["affiliations"], [{"org": "MIT", "role": "lecturer"}])
        kept = self.store.merge(keep["id"], other["id"], by="user")
        self.assertEqual(kept["relationships"], ["colleague", "friend"])
        self.assertEqual([one["org"] for one in kept["affiliations"]], ["Acme", "MIT"])
        self.assertEqual(kept["profession"], "accountant")

    def test_your_own_profession_and_organisation_wait_in_the_inbox(self) -> None:
        me = self.store.me()
        self.store.edit(me["id"], by="user", affiliations=[{"org": "Northfield University", "role": "PhD student"}])
        item = self.store.propose_for_me({"field": "profession", "value": "researcher"}, "seen")
        self.assertIn("Your profession", item["title"])
        moved = self.store.propose_for_me({"field": "affiliation", "org": "Globex",
                                           "role": "research scientist"}, "a new job")
        self.assertIn("research scientist at Globex", moved["title"])
        self.assertIsNone(self.store.propose_for_me(
            {"field": "affiliation", "org": "Northfield University", "role": "PhD student"}, "already so"))
        self.store.decide(item["id"], True)
        self.store.decide(moved["id"], True)
        me = self.store.me()
        self.assertEqual(me["profession"], "researcher")
        self.assertEqual([one["org"] for one in me["affiliations"]], ["Globex", "Northfield University"])
        self.assertEqual(me["role"], "research scientist")

    def test_the_agent_heartbeat_writes_them(self) -> None:
        said = people_step.apply(self.store, [{
            "name": "Ana Ruiz", "relationships": ["colleague", "friend", "made-up word"],
            "profession": "accountant",
            "affiliations": [{"org": "Acme", "role": "CFO"}, {"org": "MIT", "role": ""}]}])
        self.assertTrue(said[0].get("added"), said)
        ana = self.store.find("Ana Ruiz")
        self.assertEqual(ana["relationships"], ["colleague", "friend"])
        self.assertEqual(ana["profession"], "accountant")
        self.assertEqual([one["org"] for one in ana["affiliations"]], ["Acme", "MIT"])


class StepTwoKeepsTheWholeListTests(_Base):
    """Step 2 is given EVERY contact - their details, accounts and latest
    activity - and answers with what to do with each: update, add, merge or
    delete; a person added again, or activity written again, is not."""

    def test_every_contact_with_their_latest_activity(self) -> None:
        ana = self.store.add("Ana Ruiz", by="user", relationship="colleague", org="Acme",
                             profession="accountant")
        self.store.add_account(ana["id"], "LinkedIn", "", "https://linkedin.com/in/ana")
        for n in range(7):
            self.store.add_activity(ana["id"], f"thing {n}", f"2026-10-0{n + 1}")
        mei_zh = self.store.add("谭梅", by="agent")
        far = self.store.add("Someone Elsewhere", by="agent")
        said = people_step.context(self.store, "a message from 谭梅 about the draft")
        self.assertTrue(said.index("谭梅") < said.index("Ana Ruiz"), "named in the window first")
        self.assertIn("job: accountant", said)
        self.assertIn("https://linkedin.com/in/ana", said)
        self.assertIn("thing 6", said)
        self.assertIn("thing 2", said)
        self.assertNotIn("thing 1", said, "the latest five")
        self.assertIn("added by the agent, untouched", said)
        self.assertIn(far["id"], said)
        self.assertIn(mei_zh["id"], said)

    def test_past_the_room_a_line_each(self) -> None:
        from unittest import mock
        for n in range(6):
            person = self.store.add(f"Person {n}", by="agent", org="Acme")
            self.store.add_activity(person["id"], "x" * 300, "2026-10-01")
        with mock.patch.object(people_step, "MOST_CONTEXT_CHARS", 900):
            said = people_step.context(self.store, "")
        self.assertIn("listed in a line only", said)

    def test_an_add_of_someone_listed_is_an_update(self) -> None:
        ana = self.store.add("Ana Ruiz", by="user")
        self.store.add_account(ana["id"], "GitHub", "anaruiz", "https://github.com/anaruiz")
        said = people_step.apply(self.store, [{
            "action": "add", "name": "A. Ruiz",
            "accounts": [{"platform": "GitHub", "url": "https://github.com/anaruiz"}],
            "activity": [{"at": "2026-10-05", "what": "Opened a pull request"}]}])
        self.assertEqual(said[0]["person_id"], ana["id"], said)
        self.assertEqual(len(self.store.people()), 1, "not added again")
        self.assertIn("A. Ruiz", self.store.get(ana["id"])["aliases"])

    def test_activity_already_kept_is_not_written_again(self) -> None:
        ana = self.store.add("Ana Ruiz", by="user")
        entry = {"action": "update", "person_id": ana["id"], "name": "Ana Ruiz",
                 "activity": [{"at": "2026-10-05", "what": "Sent the budget"}]}
        people_step.apply(self.store, [entry])
        people_step.apply(self.store, [entry])
        self.assertEqual(len(self.store.activity(ana["id"])), 1)

    def test_merge_two_entries_that_are_one_person(self) -> None:
        keep = self.store.add("Mei Tan", by="agent")
        other = self.store.add("谭梅", by="agent")
        self.store.add_activity(other["id"], "Sent the draft", "2026-10-04")
        said = people_step.apply(self.store, [{
            "action": "merge", "person_id": keep["id"], "same_as": other["id"],
            "why": "the same person, in Chinese and in English", "name": "Mei Tan",
            "activity": [{"at": "2026-10-06", "what": "Booked the room"}]}])
        self.assertTrue(said[0]["ok"], said)
        self.assertIsNone(self.store.get(other["id"]))
        kept = self.store.get(keep["id"], with_details=True)
        self.assertIn("谭梅", kept["aliases"])
        self.assertEqual({one["what"] for one in kept["activity"]},
                         {"Sent the draft", "Booked the room"})
        refused = people_step.apply(self.store, [{"action": "merge", "person_id": keep["id"],
                                                  "same_as": keep["id"], "why": "x"}])
        self.assertIn("error", refused[0])

    def test_delete_only_the_agents_own_untouched(self) -> None:
        mine = self.store.add("My Friend", by="user")
        theirs = self.store.add("A Columnist", by="agent")
        edited = self.store.add("Bo", by="agent")
        self.store.edit(edited["id"], by="user", relationship="friend")
        said = people_step.apply(self.store, [
            {"action": "delete", "person_id": one["id"], "why": "a name from the news"}
            for one in (mine, theirs, edited)] + [
            {"action": "delete", "person_id": theirs["id"]}])
        self.assertIn("theirs to delete", said[0]["error"])
        self.assertTrue(said[1]["ok"], said)
        self.assertIn("theirs to delete", said[2]["error"])
        self.assertTrue(self.store.get(theirs["id"])["deleted"])
        self.assertFalse(self.store.get(mine["id"])["deleted"])
        self.assertEqual(self.store.actions("agent")[0]["reason"], "a name from the news")
        # Never the user themselves.
        me = self.store.me()
        said = people_step.apply(self.store, [{"action": "delete", "person_id": me["id"],
                                               "why": "x"}])
        self.assertEqual(said[0]["error"], "the user themselves")

    def test_the_instruction_says_how(self) -> None:
        said = " ".join(people_step.INSTRUCTION.split())
        for words in ("WITHOUT DUPLICATES", '"update"', '"add"', '"merge"', '"delete"',
                      "谭梅 / Mei Tan", "not in other words either",
                      "added by the agent, untouched"):
            self.assertIn(words, said)

if __name__ == "__main__":
    unittest.main()

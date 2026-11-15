"""A PERSON NOT AMONG THE USER'S CONTACTS, LOOKED FOR ON THE AGENTS SOCIETY AND JUDGED (the user, 2026-11-15:
"conatct没有他会去平台上(agentsociety搜索)， 搜到人之后根据信息判断是不是这个人，不确定要给用户确认，能自己判断就是就发送"):
the site's directory searched by the name; who found is the person judged against what the user's Connections
know of them - sure only on facts that match, never a name alone. The chat has the tool too. Fictional people
on a stand-in site (society_fake.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401

from personalized_agent.data_manage.storage.database import Database
from personalized_agent.society.service import SocietyService

from society_fake import SERVER, FakeSociety
from test_society import _Config, _join

ROOT = Path(__file__).resolve().parents[1]


class _Book:
    """The user's Connections: people, their accounts (each with where it came from), what the user calls them."""

    def __init__(self, github_source: str = "added by you") -> None:
        self.people_ = {"p_wren": {"id": "p_wren", "name": "Wren Alder", "aliases": [], "called": [],
                                   "relationships": ["friend"], "is_self": False, "deleted": False,
                                   "affiliations": [{"org": "Harbour Rowing Club", "role": "coach"}],
                                   "professions": [], "locations": ["Port Elwyn"],
                                   "summary": "Coaches rowing; met at the regatta.",
                                   "activity": [{"what": "asked about the autumn regatta"}]}}
        self.accounts_ = {"p_wren": [{"platform": "GitHub", "handle": "wren-alder",
                                      "url": "https://github.com/wren-alder", "source": github_source}]}

    def me(self):
        return {"name": "Mira Holt"}

    def avatar_path(self, person):
        return None

    def get(self, person_id, with_details=False):
        person = self.people_.get(person_id)
        return dict(person, accounts=list(self.accounts_.get(person_id, []))) if person else None

    def accounts(self, person_id):
        return [dict(one) for one in self.accounts_.get(person_id, [])]

    def find(self, name="", accounts=(), include_deleted=True):
        for asked in accounts or []:
            for ident, mine in self.accounts_.items():
                if any(one["handle"] == asked.get("handle") and one["platform"] == asked.get("platform")
                       for one in mine):
                    return self.get(ident)
        return next((self.get(one["id"]) for one in self.people_.values()
                     if name and name.lower() in {one["name"].lower(), *[a.lower() for a in one["aliases"]]}),
                    None)

    def search(self, query="", most=20):
        return [self.get(one["id"]) for one in self.people_.values()
                if query and query.lower().split()[0] in one["name"].lower()][:most]

    def people(self, include_deleted=False):
        return [self.get(ident) for ident in self.people_]

    def add(self, name, by="user", reason="", **fields):
        ident = f"p_{len(self.people_) + 1}"
        self.people_[ident] = {"id": ident, "name": name, "aliases": [], "called": [], "relationships": [],
                               "is_self": False, "deleted": False, "affiliations": [], "professions": [],
                               "locations": [], "summary": "", "activity": []}
        return self.get(ident)

    def add_account(self, person_id, platform="", handle="", url="", source="", found_at=""):
        mine = self.accounts_.setdefault(person_id, [])
        if not any(one["handle"] == handle and one["platform"] == platform for one in mine):
            mine.append({"platform": platform, "handle": handle, "url": url, "source": source})
        return {"id": "a"}

    def add_called(self, person_id, words):
        self.people_[person_id]["called"] += list(words)
        return self.people_[person_id]["called"]

    def writing(self, person_id):
        return []


class _Models:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def invoke(self, request, role=""):
        self.asked.append((request, role))
        said = self.answer(request) if callable(self.answer) else self.answer
        return SimpleNamespace(text=said, stop_reason="end_turn")


class FindAPersonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.server = FakeSociety()
        folder = Path(self._dir.name)
        self.models = _Models("{}")
        self.app = SimpleNamespace(db=Database(folder / "a" / "agent.db"),
                                   paths=SimpleNamespace(credentials_dir=folder / "a" / "credentials"),
                                   config=_Config(society_server=SERVER), connections=_Book(), models=self.models)
        (folder / "a").mkdir(exist_ok=True)
        self.mira = SocietyService(self.app, self.server.transport)
        self.addCleanup(self.mira.db.close)
        other = SimpleNamespace(db=Database(folder / "b" / "agent.db"),
                                paths=SimpleNamespace(credentials_dir=folder / "b" / "credentials"),
                                config=_Config(society_server=SERVER), connections=_Book())
        self.wren_side = SocietyService(other, self.server.transport)
        self.addCleanup(self.wren_side.db.close)
        _join(self.server, self.mira, "Mira Holt")
        self.wren = _join(self.server, self.wren_side, "Wren Alder", listed=True)

    def _claims(self, *claimed) -> None:
        self.server.accounts[self.wren]["claimed"] = [dict(one) for one in claimed]

    def _says_sure(self) -> None:
        self.models.answer = lambda request: json.dumps(
            {"match": self.wren, "sure": True, "verified_account": "https://github.com/wren-alder",
             "why": "Their verified GitHub is the one the user's Connections hold."})

    def test_sure_on_a_verified_account_the_connections_hold(self) -> None:
        # The user, 2026-11-15: "你要告诉agent基于verification判断，另外让agent理解verification的机制".
        self._claims({"kind": "github", "where": "https://github.com/wren-alder/", "verified": True,
                      "verified_at": 1794000000})
        self._says_sure()
        said = self.mira.find_agents("Wren Alder")
        [found] = said["agents"]
        self.assertEqual((found["agent"], found["sure"]), (self.wren, True))
        self.assertEqual(said["judged"]["verified_account"], "https://github.com/wren-alder/")
        request, role = self.models.asked[0]
        self.assertEqual(role, "primary", "the usual model, not the small one")
        handed = request.messages[0]["content"]
        self.assertIn("Harbour Rowing Club", handed, "what the user's Connections know is weighed")
        self.assertIn("verified_at", handed)
        self.assertIn("HOW THE SITE VERIFIES AN ACCOUNT", request.system)
        self.assertIn("Nothing else", request.system)

    def test_the_models_sure_held_to_verification_in_code(self) -> None:
        # Their word only - an unverified claim of the very account, or a verified one the user does not know,
        # or the same club and role: not sure, whatever the model said.
        for claimed in ({"kind": "github", "where": "https://github.com/wren-alder", "verified": False},
                        {"kind": "github", "where": "https://github.com/someone-else", "verified": True},
                        None):
            self._claims(*([claimed] if claimed else []))
            self._says_sure()
            said = self.mira.find_agents("Wren Alder")
            self.assertFalse(said["judged"]["sure"], claimed)
            self.assertEqual(said["judged"]["match"], self.wren, "the likely one, for the user to confirm")
            self.assertIn("no account it claims is both verified", said["judged"]["why"])

    def test_not_sure_or_a_match_not_among_those_found_is_sure_of_no_one(self) -> None:
        for answer in ({"match": self.wren, "sure": False, "why": "Only the name matches."},
                       {"match": "pa-NONE-0000", "sure": True, "why": "made up"},
                       "not json"):
            self.models.answer = json.dumps(answer) if isinstance(answer, dict) else answer
            said = self.mira.find_agents("Wren Alder")
            self.assertFalse(any(one["sure"] for one in said["agents"]), answer)
            self.assertFalse(said["judged"]["sure"])

    # -- WHO THE USER MEANS, KEPT IN CONNECTIONS (the user, 2026-11-15: "我对某个人的称呼就是contact list上的某某人，你要
    #    把对应关系留着，在你的connections里保存"; "要不是我亲手验证，要不是根据网站上verify过的信息的match, 否则一律问我") --

    def test_what_the_user_said_is_kept_and_found_by_it_next_time(self) -> None:
        # Someone in Connections by the account's name: which one is the user's to say - nothing kept yet.
        asked = self.mira.remember_who("Little Wren", self.wren, why="the user: Little Wren is Wren Alder")
        self.assertEqual([one["person"] for one in asked["in_connections"]], ["p_wren"])
        self.assertNotIn("Agents Society", [one["platform"] for one in self.app.connections.accounts("p_wren")])
        said = self.mira.remember_who("Little Wren", self.wren, "p_wren", why="the user: yes, that Wren")
        self.assertEqual((said["person"], said["called"], said["made_new"]), ("p_wren", "Little Wren", False))
        book = self.app.connections
        self.assertIn({"platform": "Agents Society", "handle": self.wren,
                       "url": self.mira.client.page_url(self.wren), "source": "the user said so"},
                      book.accounts("p_wren"))
        self.assertEqual(book.people_["p_wren"]["called"], ["Little Wren"])
        found = self.mira.find_agents("Little Wren")
        self.assertEqual([(one["agent"], one["sure"]) for one in found["agents"]], [(self.wren, True)])
        self.assertEqual(self.models.asked, [], "known: no site search, no model")
        from personalized_agent.agent.reports import did_since
        self.assertIn("contact_named", [one["kind"] for one in did_since(self.mira.db, "")])

    def test_one_not_in_connections_is_made_when_the_user_says_it_is_none_of_them(self) -> None:
        said = self.mira.remember_who("Rowan", self.wren, "new")
        self.assertTrue(said["made_new"])
        self.assertEqual(self.app.connections.people_[said["person"]]["called"], ["Rowan"])
        self.assertIn(self.app.connections.people_[said["person"]]["name"], ("Wren Alder", "Rowan"))

    def test_an_account_the_agent_matched_is_not_sure(self) -> None:
        # Paired by a name - the agent's match, not the user's hand nor the site's verification: asked.
        self.app.connections.add_account("p_wren", platform="Agents Society", handle=self.wren,
                                         source="your contacts on the Agents Society, paired by the name")
        said = self.mira.find_agents("Wren Alder")
        mine = [one for one in said["agents"] if one.get("person") == "p_wren"]
        self.assertEqual([one["sure"] for one in mine], [False])
        self.assertIn("ask the user", mine[0]["how_known"])

    def test_a_verified_claim_is_never_matched_with_an_account_the_agent_found_on_the_web(self) -> None:
        self.app.connections = _Book(github_source="found on the public web")
        self._claims({"kind": "github", "where": "https://github.com/wren-alder", "verified": True})
        self._says_sure()
        self.assertFalse(self.mira.find_agents("Wren Alder")["judged"]["sure"])

    def test_sure_by_verification_kept_for_next_time(self) -> None:
        from personalized_agent.society.service import VERIFIED_SOURCE
        self._claims({"kind": "github", "where": "https://github.com/wren-alder", "verified": True})
        self._says_sure()
        self.assertTrue(self.mira.find_agents("Wren Alder")["judged"]["sure"])
        kept = [one for one in self.app.connections.accounts("p_wren") if one["platform"] == "Agents Society"]
        self.assertEqual([one["source"] for one in kept], [VERIFIED_SOURCE + "https://github.com/wren-alder"])
        self.models.asked.clear()
        again = self.mira.find_agents("Wren Alder")
        self.assertEqual([(one["agent"], one["sure"]) for one in again["agents"]], [(self.wren, True)])
        self.assertEqual(self.models.asked, [], "found in Connections now")

    def test_a_link_the_user_took_off_is_not_put_back_until_they_say_so(self) -> None:
        # The user, 2026-11-15: "我人工可以去connections里把它和contact 的link删掉，agent不会自动加回来（除非我又说要加回来)".
        self._claims({"kind": "github", "where": "https://github.com/wren-alder", "verified": True})
        self._says_sure()
        self.assertTrue(self.mira.find_agents("Wren Alder")["judged"]["sure"])
        book = self.app.connections
        # Taken off by the user in Connections, as its page does: the account gone, the action kept.
        book.accounts_["p_wren"] = [one for one in book.accounts_["p_wren"] if one["platform"] != "Agents Society"]
        self.mira.db.execute("CREATE TABLE IF NOT EXISTS connection_actions (id TEXT, person_id TEXT, by TEXT, "
                             "action TEXT, before TEXT, after TEXT, at TEXT)")
        self.mira.db.execute("INSERT INTO connection_actions (id, person_id, by, action, before, after, at) VALUES "
                             "('x1', 'p_wren', 'user', 'remove a profile', ?, '{}', 'now')",
                             (json.dumps({"account": {"platform": "Agents Society", "handle": self.wren}}),))
        said = self.mira.find_agents("Wren Alder")
        self.assertFalse(said["judged"]["sure"])
        self.assertIn("took this Agents Society account off", said["judged"]["why"])
        self.assertNotIn("Agents Society", [one["platform"] for one in book.accounts("p_wren")], "not put back")
        # Said again by the user: theirs again.
        self.mira.remember_who("Wren", self.wren, "p_wren", why="the user: yes, keep her")
        self.assertFalse(self.mira._taken_off("p_wren", self.wren))
        again = self.mira.find_agents("Wren Alder")
        self.assertEqual([(one["agent"], one["sure"]) for one in again["agents"]], [(self.wren, True)])

    def test_no_one_by_that_name_no_model_asked(self) -> None:
        said = self.mira.find_agents("Nobody Byname")
        self.assertEqual(said["agents"], [])
        self.assertIn("no one by that name", said["judged"]["why"])
        self.assertEqual(self.models.asked, [])

    def test_the_chat_has_it_and_the_agent_knows_it_can_write_to_people(self) -> None:
        from personalized_agent.agent.core.tools import ToolRegistry
        from personalized_agent.society import tools as society_tools
        registry = ToolRegistry()
        society_tools.add_tools(registry, self.mira)
        self.assertTrue(registry.has("society_find_agent"))
        self.assertTrue(registry.has("society_remember_who"))
        work = (ROOT / "personalized_agent" / "agent" / "work" / "tools.py").read_text("utf-8")
        self.assertNotIn("society_remember_who", work, "the user is not there to say it")
        self.assertIn("NOT SURE: write to no one yet", society_tools.FIND_SAYS)
        core = (ROOT / "personalized_agent" / "agent" / "core" / "agent_core.py").read_text("utf-8")
        self.assertNotIn("The agent never contacts anyone.", core)
        self.assertIn("Agents Society: the one way the agent writes to other people", core)


if __name__ == "__main__":
    unittest.main()

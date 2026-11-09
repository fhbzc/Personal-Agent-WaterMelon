"""THE CHAT'S SEARCHES (the user, 2026-11-07): read-only tools for the full agent - the to-dos,
the projects and tasks, what was learned, what the user was doing - each giving one copy of each
thing, without mechanical lines or the agent's own records, within a cap; the quick voice has
none, and is told in a sentence or two what the full agent can do with its tools."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.agent.core import memory_tools as mt
from personalized_agent.agent.core.agent_core import SYSTEM_PROMPT
from personalized_agent.agent.two_speed import QUICK_RULES
from personalized_agent.heartbeat.states import task_projects as tp
from personalized_agent.heartbeat.states.tasks import Kept, Task


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


class _WithData(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.db = self.app.db
        self.now = datetime.now(timezone.utc)

    def call(self, name, **args):
        said = self.app.agent.tools.call(name, args)
        self.assertIsInstance(said, str, said)
        return said

    def todo(self, text, detail="", done=False, days_ago=1):
        made = self.app.todos.add(text, detail)
        at = _iso(self.now - timedelta(days=days_ago))
        self.db.execute("UPDATE todo_items SET created_at = ?, done_at = ? WHERE id = ?",
                        (at, at if done else "", made["id"]))
        return made["id"]

    def state(self, ident, title, detail="", hours_ago=2.0, about="user", source="agent",
              kind="work"):
        start = self.now - timedelta(hours=hours_ago)
        self.db.execute(
            "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
            "created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, 'done', ?, ?, ?)",
            (ident, title, detail, source, about, _iso(start), _iso(start + timedelta(minutes=30)),
             _iso(start), _iso(start), json.dumps({"kind": kind})))

    def project(self, ident, title, line, tasks=(), steps=(), archived=False):
        row = Task(id=ident, number=abs(hash(ident)) % 1000, seat=1)
        row.profile = {"scheme": tp.SCHEME, "last_seen": _iso(self.now - timedelta(days=1))}
        row.described = {"title": title, "line": line, "summary": line, "minutes": 120.0,
                         "kind": "work", "todo": [dict(one, id=f"s{n}") for n, one in
                                                  enumerate(steps)]}
        if archived:
            row.archived_at = _iso(self.now)
        Kept(self.db, "live").save([row])
        tp.RunTasks(self.db, "live").add([dict({
            "minutes": 30.0, "states": [], "ends_at": _iso(self.now - timedelta(days=2)),
            "project_id": ident, "kind": "work"}, **one) for one in tasks])

    def snapshot(self):
        tables = [one["name"] for one in self.db.query(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {name: self.db.query_one(f"SELECT COUNT(*) AS n FROM \"{name}\"")["n"]
                for name in tables}


class TheToolsAreOfferedToTheFullAgentAlone(_WithData):

    def test_the_full_agent_is_offered_every_search(self) -> None:
        offered = []
        invoke = self.app.models.invoke

        def watching(request, *args, **kwargs):
            if request.task == "agent_instruction":
                offered.append({spec.name for spec in request.tools or []})
            return invoke(request, *args, **kwargs)
        self.app.models.invoke = watching
        self.app.agent.run_instruction("what was I doing this morning?")
        self.assertTrue(offered)
        for name in mt.NAMES:
            self.assertIn(name, offered[0])
        for spec in mt.SPECS:
            self.assertTrue(spec.read_only)
            self.assertIn("Use it", spec.description, "each says when to use it")

    def test_the_quick_voice_has_no_tools_and_a_sentence_or_two_on_them(self) -> None:
        asked = []
        answer = SimpleNamespace(text='{"reply": "Looking.", "full_agent": "run"}',
                                 stop_reason="end_turn", tool_calls=[], raw_content=None)

        def invoke(request, role=None):
            asked.append(request)
            return answer
        self.app.two_speed.models = SimpleNamespace(invoke=invoke)
        self.app.two_speed._quick("what is on my list?", [], [])
        request, = asked
        self.assertEqual(request.tools, [])
        self.assertIn("With its tools the full agent can search the user's to-dos", request.system)
        # Summed up, not a line for each tool.
        for name in mt.NAMES:
            self.assertNotIn(name, QUICK_RULES)
        summary = QUICK_RULES[QUICK_RULES.index("With its tools"):QUICK_RULES.index(
            "Answer with JSON")]
        self.assertLessEqual(summary.count(". "), 2)

    def test_the_prompt_no_longer_says_there_is_no_memory(self) -> None:
        self.assertNotIn("no memory", SYSTEM_PROMPT)
        for name in mt.NAMES:
            self.assertIn(name, SYSTEM_PROMPT)


class TheTodos(_WithData):

    def test_found_by_words_open_first_done_too_once_each(self) -> None:
        self.todo("Book the ferry to Lundy", "for the survey weekend")
        self.todo("Book the ferry to Lundy", "for the survey weekend")
        self.todo("Return the ferry tickets", done=True, days_ago=20)
        self.todo("Water the ficus")
        said = self.call("search_todos", query="ferry")
        self.assertEqual(said.count("Book the ferry to Lundy"), 1)
        self.assertIn("Return the ferry tickets", said)
        self.assertLess(said.index("Book the ferry"), said.index("Return the ferry"))
        self.assertNotIn("ficus", said)
        self.assertNotIn("Return the ferry", self.call("search_todos", query="ferry",
                                                       include_done=False))
        self.assertIn("ficus", self.call("search_todos"))


class TheWork(_WithData):

    def test_a_project_with_its_status_open_steps_and_tasks(self) -> None:
        self.project("proj_tide", "Tide tables", "A study of the tides at Porthcawl",
                     tasks=[{"id": "t1", "title": "Plotted the spring tides",
                             "summary": "Charts of the spring tides"}],
                     steps=[{"text": "Send the charts to the harbour office"},
                            {"text": "Draft the method", "done": True}])
        self.project("proj_bees", "Beekeeping log", "Notes on the hives", archived=True,
                     tasks=[{"id": "t2", "title": "Counted the frames"}])
        said = self.call("search_work", query="tides")
        self.assertIn("Tide tables", said)
        self.assertIn("active", said)
        self.assertIn("Send the charts to the harbour office", said)
        self.assertNotIn("Draft the method", said, "a finished step is not open")
        self.assertIn("Plotted the spring tides", said)
        self.assertNotIn("Beekeeping", said)
        bees = self.call("search_work", query="frames")
        self.assertIn("Beekeeping log", bees)
        self.assertIn("dormant", bees)

    def test_the_agents_own_and_mechanical_tasks_are_left_out(self) -> None:
        self.project("proj_tide", "Tide tables", "A study of the tides",
                     tasks=[{"id": "t1", "title": "Plotted the spring tides"},
                            {"id": "t2", "title": "1886 files edited"},
                            {"id": "t3", "title": "The agent checked the tide tables reminder"}])
        said = self.call("search_work", query="tide")
        self.assertIn("Plotted the spring tides", said)
        self.assertNotIn("1886 files", said)
        self.assertNotIn("The agent checked", said)


class TheLessons(_WithData):

    def rule(self, ident, statement, state="active", kind="general"):
        self.db.execute("INSERT INTO lesson_rules (id, kind, statement, state, confidence, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, 0.9, ?, ?)",
                        (ident, kind, statement, state, _iso(self.now), _iso(self.now)))

    def test_by_their_words_without_a_retriever(self) -> None:
        self.app.agent.lesson_retriever = None
        self.rule("r1", "Keep replies to the harbour office short and formal")
        self.rule("r2", "Keep replies to the harbour office short and formal", kind="work")
        self.rule("r3", "Never schedule anything on Sunday mornings")
        self.rule("r4", "Write replies to the harbour office in prose", state="retired")
        self.db.execute("INSERT INTO lesson_evidence (id, kind, at, action, summary, english, "
                        "reason) VALUES ('e1', 'general', ?, 'edited', '', "
                        "'The user shortened a reply to the harbour office', 'too long')",
                        (_iso(self.now),))
        said = self.call("search_lessons", query="reply to the harbour office")
        self.assertEqual(said.count("Keep replies to the harbour office short and formal"), 1)
        self.assertIn("shortened a reply", said)
        self.assertIn("too long", said)
        self.assertNotIn("Sunday", said)
        self.assertNotIn("in prose", said, "a retired rule is not given")

    def test_by_the_retriever_at_the_chats_place_when_it_takes_one(self) -> None:
        calls = []

        class WithSite:
            def text(self, kinds, moment, rules=8, examples=4, rewrite_task="", site=""):
                calls.append(site)
                return "- [r1] Keep replies short (follow)\n- [r1] Keep replies short (follow)"

        class WithoutSite:
            def text(self, kinds, moment, rules=8, examples=4, rewrite_task=""):
                calls.append("none")
                return "- [r2] Sundays are quiet (lean on it)"
        self.app.agent.lesson_retriever = WithSite()
        said = self.call("search_lessons", query="a reply")
        self.assertEqual(said.count("Keep replies short"), 1)
        self.app.agent.lesson_retriever = WithoutSite()
        self.assertIn("Sundays are quiet", self.call("search_lessons", query="Sunday"))
        self.assertEqual(calls, ["chat", "none"])

    def test_the_applications_own_retriever_works_too(self) -> None:
        self.rule("r1", "Keep replies to the harbour office short and formal")
        self.app.agent.lesson_retriever = self.app.lesson_retriever
        self.assertIn("harbour office", self.call("search_lessons", query="harbour office reply"))


class TheActivity(_WithData):

    def test_a_time_range_without_noise_repeats_or_the_agents_own(self) -> None:
        self.state("s1", "The user is plotting the spring tides", "Charts in a notebook", 3)
        self.state("s2", "The user is plotting the spring tides", "Charts in a notebook", 2.5)
        self.state("s3", "16 pages on www.example-shop.com", "", 2.4)
        self.state("s4", "firefox.exe - 13:39", "", 2.3)
        self.state("s5", "The agent worked on the tide reminder", "", 2.2, about="agent")
        self.state("s6", "Scheduled: Has the tide chart moved?", "", 2.1, source="task")
        self.state("s7", "The user read about kelp forests", "", 40)
        said = self.call("search_activity")
        self.assertEqual(said.count("plotting the spring tides"), 1)
        self.assertIn("1 more time like it", said)
        for left_out in ("16 pages", "firefox.exe", "The agent worked", "Scheduled:", "kelp"):
            self.assertNotIn(left_out, said)
        self.assertIn("kelp", self.call("search_activity", query="kelp"))
        start = self.now - timedelta(hours=41)
        from personalized_agent.scheduler.core import zones
        wall = zones.wall_of(start, zones.here()).strftime("%Y-%m-%d %H:%M")
        until = zones.wall_of(start + timedelta(hours=2), zones.here()).strftime("%Y-%m-%d %H:%M")
        ranged = self.call("search_activity", from_local=wall, until_local=until)
        self.assertIn("kelp", ranged)
        self.assertNotIn("spring tides", ranged)

    def test_capped_with_a_note_of_how_many_more(self) -> None:
        for n in range(60):
            self.state(f"s{n}", f"The user is reading chapter {n} of the tide almanac",
                       "x " * 400, hours_ago=1 + n / 100)
        said = self.call("search_activity", query="almanac", limit=50)
        self.assertLessEqual(len(said), mt.MOST_CHARS + 200)
        self.assertIn("more not shown", said)


class NothingIsWritten(_WithData):

    def test_every_search_only_reads(self) -> None:
        self.todo("Book the ferry")
        self.state("s1", "The user is plotting the spring tides")
        self.project("proj_tide", "Tide tables", "A study", tasks=[{"id": "t1", "title": "Plot"}])
        before = self.snapshot()
        for name in mt.NAMES:
            self.app.agent.tools.call(name, {"query": "tide"})
            self.app.agent.tools.call(name, {})
        after = self.snapshot()
        # THE ONE WRITE ALLOWED: each retrieval is recorded where the build carries the Debugger
        # (the user, 2026-11-07: "debugger 版用的是存的"). Nothing of the user's is touched.
        logged = after.pop("lesson_retrievals", 0) - before.pop("lesson_retrievals", 0)
        self.assertEqual(after, before)
        from personalized_agent.build_flags import debugger_available
        if debugger_available():
            self.assertGreaterEqual(logged, 1, "search_lessons recorded")
        with self.assertRaises(PermissionError):
            mt.ReadOnly(self.db).execute("DELETE FROM todo_items")
        with self.assertRaises(PermissionError):
            mt.ReadOnly(self.db).query("DELETE FROM todo_items")


class TheNoise(unittest.TestCase):

    def test_what_counts_as_mechanical(self) -> None:
        for line in ("1886 files edited", "16 pages on www.amazon.com", "firefox.exe - 13:39",
                     "Slack — 09:02", "chrome.exe", ""):
            self.assertTrue(mt.noise(line), line)
        for line in ("Meeting with the harbour office at 13:30", "Edited 3 chapters of the thesis",
                     "The user is plotting the spring tides"):
            self.assertFalse(mt.noise(line), line)


if __name__ == "__main__":
    unittest.main()

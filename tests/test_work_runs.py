"""The agent working on the user's work: it reads widely and writes in one
place - the work's own folder - and that is enforced, not asked."""

from __future__ import annotations

import unittest
from pathlib import Path

from support import TemporaryAgent


class AWorkRunWritesOnlyInItsFolderTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        from personalized_agent.agent.work.runner import WorkRuns
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime

        self.folder = self.app.work.folder_for("The agent release", "project-abc123")
        runtime = SandboxRuntime(self.app.work.policy_for(self.folder),
                                 platform=self.app.platform)
        self.tools = WorkTools(runtime)
        self.workspace = Path(self.app.policy.workspace_root)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_its_folder_is_under_agent_work_in_the_workspace(self) -> None:
        self.assertEqual(self.folder.parent, self.workspace / "agent_work")
        self.assertTrue(self.folder.name.startswith("the_agent_release-"))

    def test_a_file_inside_is_written(self) -> None:
        said = self.tools.call("write_file", {"path": "notes/plan.md",
                                              "content": "hello"})
        self.assertNotIn("error", said, said)
        self.assertEqual((self.folder / "notes" / "plan.md").read_text(), "hello")

    def test_a_file_anywhere_else_is_refused(self) -> None:
        other = self.app.work.folder_for("Other work", "project-zzz999")
        other.mkdir(parents=True, exist_ok=True)
        for path in ("../escaped.md",
                     str(self.workspace / "documents" / "x.md"),
                     str(self.workspace / "agent" / "x.md"),
                     str(other / "x.md"),
                     str(Path.home() / "pa-test-escape.md")):
            said = self.tools.call("write_file", {"path": path, "content": "no"})
            self.assertEqual(said.get("error"), "security_violation", (path, said))
        self.assertFalse((self.folder.parent / "escaped.md").exists())
        self.assertFalse((other / "x.md").exists())
        self.assertFalse((Path.home() / "pa-test-escape.md").exists())

    def test_code_it_runs_can_write_inside_and_nowhere_else(self) -> None:
        target = self.workspace / "documents" / "from-code.md"
        said = self.tools.call("run_python", {"code": (
            "open('inside.txt', 'w').write('ok')\n"
            "try:\n"
            f"    open(r'{target}', 'w').write('no')\n"
            "    print('WROTE OUTSIDE')\n"
            "except Exception as exc:\n"
            "    print('refused:', type(exc).__name__)\n")})
        self.assertTrue((self.folder / "inside.txt").exists(), said)
        self.assertFalse(target.exists())
        self.assertNotIn("WROTE OUTSIDE", str(said.get("stdout")))

    def test_it_can_read_but_not_the_applications_own_state(self) -> None:
        readme = self.workspace / "documents" / "readme.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text("the user's file")
        self.assertEqual(self.tools.call("read_file", {"path": str(readme)})["text"],
                         "the user's file")
        internal = Path(self.app.paths.data_dir) / "settings.json"
        said = self.tools.call("read_file", {"path": str(internal)})
        self.assertEqual(said.get("error"), "security_violation", said)


class GrantedFoldersTests(unittest.TestCase):
    """A folder the user granted is written as freely as the work's own -
    changed and deleted - and nothing else is; code it runs opens no
    connection."""

    def setUp(self) -> None:
        import tempfile
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        # Resolved: an 8.3 short name (C:\Users\ABCDEF~1) cannot be opened
        # from inside an AppContainer - expanding it means listing C:\Users.
        self.granted = Path(tempfile.mkdtemp(prefix="pa-granted-")).resolve()
        (self.granted / "mine.txt").write_text("the user's own")
        said = self.app.work.grant("project-abc123", str(self.granted))
        self.assertIn("delete", said["warning"])
        self.folder = self.app.work.folder_for("The agent release", "project-abc123")
        self.changes = []
        runtime = SandboxRuntime(
            self.app.work.policy_for(self.folder, self.app.work.folders("project-abc123")),
            platform=self.app.platform)
        self.tools = WorkTools(runtime, changed=lambda action, paths, said:
                               self.changes.append((action, paths)))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.granted, ignore_errors=True)
        self.agent.cleanup()

    def test_a_granted_folder_can_be_changed_and_emptied(self) -> None:
        said = self.tools.call("write_file", {"path": str(self.granted / "mine.txt"),
                                              "content": "changed"})
        self.assertNotIn("error", said, said)
        self.assertEqual((self.granted / "mine.txt").read_text(), "changed")
        said = self.tools.call("delete_file", {"path": str(self.granted / "mine.txt")})
        self.assertNotIn("error", said, said)
        self.assertFalse((self.granted / "mine.txt").exists())
        # The folder itself stays.
        said = self.tools.call("delete_file", {"path": str(self.granted)})
        self.assertIn("error", said)
        self.assertTrue(self.granted.exists())
        # Each change reported - to be recorded as the agent's.
        self.assertEqual([action for action, _paths in self.changes], ["wrote", "deleted"])

    def test_code_writes_in_a_granted_folder_and_reaches_no_network(self) -> None:
        said = self.tools.call("run_python", {"code": (
            f"open(r'{self.granted / 'made.txt'}', 'w').write('ok')\n"
            "import socket\n"
            "for host in ('127.0.0.1', '93.184.215.14'):\n"
            "    try:\n"
            "        socket.create_connection((host, 9), timeout=1)\n"
            "        print('CONNECTED')\n"
            "    except Exception as exc:\n"
            "        print('refused:', host, type(exc).__name__)\n")})
        self.assertTrue((self.granted / "made.txt").exists(), said)
        self.assertNotIn("CONNECTED", str(said.get("stdout")))
        # Out of this computer the guard refuses it; on loopback too, unless
        # the system gives the run one of its own - which no server of
        # anyone else's is on (tests/test_own_loopback.py).
        self.assertIn("93.184.215.14 SandboxViolation", str(said.get("stdout")))
        from personalized_agent.runtime.isolation import own_loopback
        if not own_loopback():
            self.assertIn("127.0.0.1 SandboxViolation", str(said.get("stdout")))
        made = (self.granted / "made.txt").resolve()
        self.assertTrue(any(Path(one).resolve() == made for one in said["files_changed"]))

    def test_some_folders_cannot_be_granted(self) -> None:
        home = Path.home()
        self.assertIn("error", self.app.work.grant("p", str(home)))
        self.assertIn("error", self.app.work.grant("p", home.anchor))
        internal = Path(self.app.policy.protected_paths[0])
        self.assertIn("error", self.app.work.grant("p", str(internal)))
        self.assertIn("error", self.app.work.grant("p", str(home / "no-such-folder-x")))


class _TwoAgents:
    """A work agent and a reviewer, answering by what they are asked: a
    contract, rounds that write notes.md, the reviews, and the deck."""

    def __init__(self, rounds_to_complete=1, gate_fail_first=False,
                 stall_always=False, blocked=False, queued_once=False,
                 final_finding_once=False, full_review="milestone", full_fails=0):
        self.asked = []
        #: When the reviewer asks for a full review: "milestone" - when the
        #: card it reviewed is done; "always"; "never". The first
        #: `full_fails` of them do not pass.
        self.full_review = full_review
        self.full_fails = full_fails
        self.full_reviews = 0
        self.rounds_to_complete = rounds_to_complete
        self.gate_fail_first = gate_fail_first
        self.stall_always = stall_always
        self.blocked = blocked
        self.queued_once = queued_once
        self.final_finding_once = final_finding_once
        self.summaries = 0
        self.reviewed_rounds = 0
        self.final_reviews = 0

    @staticmethod
    def _reply(text="", calls=None):
        reply = type("R", (), {})()
        reply.text, reply.tool_calls = text, calls or []
        reply.raw_content, reply.model, reply.provider = text, "test-model", "t"
        reply.usage = None
        return reply

    @staticmethod
    def _card(text, title):
        import re
        found = re.search(r"\[(card_[0-9a-zA-Z]+)\] " + re.escape(title), text)
        return found.group(1) if found else ""

    def _summary(self, first):
        import json as _json
        self.summaries += 1
        card = self._card(first, "The notes")
        if self.blocked:
            return {"card": card, "objective": "send the notes", "result": "cannot",
                    "claims": [], "blocked": {"card": card,
                                              "missing": "the mail server's address"}}
        bad = self.gate_fail_first and self.summaries == 1
        return {"card": card, "kind": "execution", "objective": "write the notes",
                "result": "wrote notes.md",
                "claims": [{"claim": "notes.md says hello",
                            "evidence": [{"path": "missing.md" if bad else "notes.md"}]}],
                "open": [], "decisions": [{"about": "the greeting", "chose": "hello",
                                           "over": "hi", "why": "plainer"}],
                "complete": True}

    def invoke(self, request, role=""):
        import json as _json
        from personalized_agent.models.base import ToolCall
        self.asked.append(request)
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else _json.dumps(first)
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else _json.dumps(last)
        if "You review a work agent" in request.system:
            if "REVIEW THIS CONTRACT" in said:
                return self._reply(_json.dumps({
                    "verdict": "approve", "comments": [],
                    "deliverables": [{"ref": "d1", "criteria": [
                        {"text": "notes.md says hello", "because": ["own judgement"],
                         "backed_by": "check"}], "approach": "write it plainly"}]}))
            if "A CARD HAS OPENED" in said:
                return self._reply(_json.dumps({"criteria": [
                    {"text": "the summary is one line", "because": ["own judgement"],
                     "backed_by": "own judgement"}], "approach": "keep it short"}))
            if "REVIEW THIS ROUND" in said:
                self.reviewed_rounds += 1
                findings = []
                if self.queued_once and self.reviewed_rounds == 1:
                    findings = [{"say": "a summary would help", "type": "queued",
                                 "because": ["own judgement"],
                                 "card": {"title": "A summary",
                                          "statement": "a one-line summary exists"}}]
                if self.blocked:
                    return self._reply(_json.dumps({"findings": [], "progress": "stalled",
                                                    "blocked_confirmed": True}))
                if self.stall_always:
                    return self._reply(_json.dumps({"findings": [{"say": "nothing changed",
                                                                  "type": "mainline"}],
                                                    "progress": "stalled"}))
                done = self.reviewed_rounds >= self.rounds_to_complete
                full = self.full_review == "always" or (self.full_review == "milestone" and done)
                return self._reply(_json.dumps({
                    "findings": findings, "progress": "advanced", "card_done": done,
                    "checked": ["notes.md"], "complete": done,
                    "full_review": {"now": full, "why": "the notes are done" if done
                                    else "a point to keep"},
                    "decisions": [{"n": 0, "support": "some", "because": ["own judgement"]}]}))
            if "Check this card, one of those the rounds touched" in said and \
                    "DO THE FULL REVIEW NOW" not in said:
                return self._reply(_json.dumps({"card": "", "holds": "yes",
                                                "why": "opened notes.md", "findings": []}))
            if "Every card the rounds touched is checked" in said:
                self.full_reviews += 1
                return self._reply(_json.dumps({
                    "promises": [], "reopen": [], "findings": [],
                    "passed": self.full_reviews > self.full_fails, "stop": False}))
            if "DO THE FULL REVIEW NOW" in said and "Check this card" in said:
                return self._reply(_json.dumps({"card": "", "holds": "yes",
                                                "why": "opened notes.md", "findings": []}))
            if "DO THE FULL REVIEW NOW" in said:
                import re
                self.full_reviews += 1
                passed = self.full_reviews > self.full_fails
                # One that does not pass opens the notes again, and says why.
                found = re.search(r"\[(card_[0-9a-zA-Z]+)\] The notes",
                                  _json.dumps(request.messages, default=str))
                return self._reply(_json.dumps({
                    "promises": [], "cards": [],
                    "reopen": [] if passed or not found else [
                        {"card": found.group(1), "why": "the greeting is gone"}],
                    "findings": [] if passed else [
                        {"say": "notes.md lost its greeting", "type": "mainline"}],
                    "passed": passed, "stop": False}))
            if "THE GOAL IS MET BY YOUR LAST REVIEW" in said:
                self.final_reviews += 1
                if self.final_finding_once and self.final_reviews == 1:
                    return self._reply(_json.dumps({"findings": [
                        {"say": "notes.md has no title", "severity": "high",
                         "file": "notes.md"}]}))
                return self._reply(_json.dumps({"findings": []}))
            if "CHECK THE RESULTS DECK" in said:
                return self._reply(_json.dumps({"verdict": "accepted", "comments": []}))
            return self._reply("{}")
        if "DRAFT THE CONTRACT" in first:
            return self._reply(_json.dumps({
                "goal": "notes.md says hello", "scope": ["the notes"],
                "out_of_scope": ["anything else"],
                "deliverables": [{"ref": "d1", "title": "The notes", "type": "document",
                                  "statement": "notes.md says hello",
                                  "why": "it is what was asked"}]}))
        if "MAKE THE RESULTS DECK" in first:
            return self._reply(_json.dumps({
                "title": "The notes", "summary": "notes.md, saying hello",
                "deliverables": [{"name": "The notes", "location": "notes.md",
                                  "what": "the greeting", "card": self._card(first, "The notes")}],
                "results": [{"title": "The greeting", "shows": "what it says",
                             "variables": [{"name": "greeting", "meaning": "the word",
                                            "value": "hello", "source": "notes.md line 1"}]}]}))
        if "WORK ONE ROUND" in first:
            if "FAILED THE CHECKS" in said or "THE RUN IS PAUSING HERE" in said \
                    or isinstance(last, list):
                return self._reply(_json.dumps(self._summary(first)))
            return self._reply("", [ToolCall(id="c1", name="write_file",
                                             arguments={"path": "notes.md",
                                                        "content": "hello"})])
        return self._reply("{}")



class _Promised(_TwoAgents):
    """The same two agents, with a contract that makes a core promise and a
    polish deliverable: the work agent reaches for the polish first, and the
    reviewer finds the promise broken once before it holds."""

    def _summary(self, first):
        said = super()._summary(first)
        if self.summaries == 1:
            said["card"] = self._card(first, "The colours")
        return said

    def invoke(self, request, role=""):
        import json as _json
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else _json.dumps(first)
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else _json.dumps(last)
        reviewer = "You review a work agent" in request.system
        if "You TEST a piece of work" in request.system:
            self.asked.append(request)
            return self._reply(_json.dumps({
                "promises": [{"id": "P1", "holds": "yes", "tried": "read notes.md"}],
                "findings": [{"say": "no title", "severity": "low", "file": "notes.md"}]}))
        if not reviewer and "DRAFT THE CONTRACT" in first:
            self.asked.append(request)
            return self._reply(_json.dumps({
                "goal": "notes.md says hello", "scope": ["the notes"],
                "out_of_scope": ["anything else"],
                "core_promises": [{"id": "P1", "promise": "a reader opening notes.md is greeted",
                                   "from_words": "write the notes",
                                   "real_condition": "open notes.md as the user would",
                                   "fails_if": ["the file is empty"]}],
                "deliverables": [
                    {"ref": "d1", "title": "The notes", "type": "document",
                     "statement": "notes.md says hello", "why": "asked",
                     "tier": "core", "promises": ["P1", "P9"]},
                    {"ref": "d2", "title": "The colours", "type": "document",
                     "statement": "the notes are in colour", "why": "nicer",
                     "tier": "polish"}]}))
        if reviewer and "REVIEW THIS ROUND" in said:
            self.asked.append(request)
            self.reviewed_rounds += 1
            holds = "no" if self.reviewed_rounds == 1 else "yes"
            return self._reply(_json.dumps({
                "promises": [{"id": "P1", "holds": holds, "tried": "opened notes.md",
                              "failed": "it was empty" if holds == "no" else ""}],
                "findings": [], "progress": "advanced", "card_done": holds == "yes",
                "checked": ["notes.md"], "complete": True}))
        return super().invoke(request, role)


class _OnlyTheUser(_Promised):
    """The promise needs a real payment: the reviewer cannot try it even
    with a stand-in, and says what the user must do."""

    def invoke(self, request, role=""):
        import json as _json
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else _json.dumps(last)
        if "You TEST a piece of work" in request.system:
            self.asked.append(request)
            return self._reply(_json.dumps({
                "promises": [{"id": "P1", "holds": "needs_user",
                              "tried": "the provider's test mode passed",
                              "user_does": "make one real pledge of $1 and see it capped"}],
                "findings": []}))
        if "You review a work agent" in request.system and "REVIEW THIS ROUND" in said:
            self.asked.append(request)
            self.reviewed_rounds += 1
            return self._reply(_json.dumps({
                "promises": [{"id": "P1", "holds": "needs_user",
                              "tried": "the provider's test mode passed",
                              "user_does": "make one real pledge of $1 and see it capped"}],
                "for_user": [{"do": "Make one real pledge of $1 and see it capped",
                              "why": "a duplicate", "promise": "P1"}],
                "findings": [], "progress": "advanced", "card_done": True,
                "checked": ["notes.md"], "complete": True}))
        return super().invoke(request, role)


class _NeverHolds(_Promised):
    """The tester finds the promise broken every time it looks."""

    def __init__(self):
        super().__init__()
        self.tests = 0

    def _summary(self, first):
        # The card accepted is no longer listed open: the fixes are on it.
        said = super()._summary(first)
        if said.get("card"):
            self.card = said["card"]
        else:
            said["card"] = getattr(self, "card", "")
        return said

    def invoke(self, request, role=""):
        import json as _json
        if "You TEST a piece of work" in request.system:
            self.asked.append(request)
            self.tests += 1
            return self._reply(_json.dumps({
                "promises": [{"id": "P1", "holds": "no", "tried": "opened notes.md",
                              "failed": "no greeting"}],
                "findings": [{"say": "no greeting", "severity": "high",
                              "file": "notes.md"}]}))
        return super().invoke(request, role)

class _NeverTried(_NeverHolds):
    """The tester cannot open the work, and the final review finds nothing:
    the promise stands "untried" each time it looks."""

    def __init__(self, tried_from=None):
        super().__init__()
        self.tried_from = tried_from

    def invoke(self, request, role=""):
        import json as _json
        if "You TEST a piece of work" in request.system:
            self.asked.append(request)
            self.tests += 1
            holds = ("yes" if self.tried_from and self.tests >= self.tried_from
                     else "untried")
            return self._reply(_json.dumps({
                "promises": [{"id": "P1", "holds": holds,
                              "tried": "could not open notes.md"
                              if holds == "untried" else "read notes.md"}],
                "findings": []}))
        return super(_NeverHolds, self).invoke(request, role)


class ARunIsTwoAgentsTests(unittest.TestCase):
    """A contract, approved with criteria written from the user's work; rounds
    checked by code and judged by the reviewer; a final review; the deck."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-xyz789", "name": "Write notes", "kind": "task", "why": ""}

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _wait(self):
        import time
        for _ in range(600):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                break
            time.sleep(0.1)
        return self.app.work.runs(self.unit["id"])[0]

    def _run(self, models, instruction="write the notes", limits=None):
        self.app.work.models = models
        started = self.app.work.start(self.unit, instruction, limits=limits)
        self.assertNotIn("error", started, started)
        return self._wait(), started

    @staticmethod
    def _work_prompts(models):
        import json as _json
        return [_json.dumps(r.messages, default=str) for r in models.asked
                if "You review a work agent" not in r.system]

    def test_every_call_is_told_the_users_timezone_and_the_date(self) -> None:
        """Not told, the models wrote the times on the slides in UTC."""
        from personalized_agent.agent.work.runner import today_line, zone_rule
        models = _TwoAgents()
        self._run(models)
        self.assertTrue(models.asked)
        for request in models.asked:
            self.assertIn(zone_rule(), request.system or "")
        builders = [one for one in models.asked
                    if "You review a work agent" not in (one.system or "")]
        self.assertTrue(any(str(one.messages[0]["content"]).startswith(today_line())
                            for one in builders))
        # The zone, and no clock that changes call by call: what the provider
        # caches of the instructions stays the same.
        self.assertIn("UTC", zone_rule())
        self.assertIn("09:30:00", zone_rule())

    def test_core_promises_come_first_and_the_work_is_not_done_without_them(self) -> None:
        import json as _json
        models = _Promised()
        run, _started = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        plan = run["plan"]
        # The promise is in the contract; each deliverable has its tier,
        # and serves only promises the contract makes.
        self.assertEqual([one["id"] for one in plan["contract"]["promises"]], ["P1"])
        tiers = sorted((one["tier"], one["promises"]) for one in plan["tiers"].values())
        self.assertEqual(tiers, [("core", ["P1"]), ("polish", [])])
        # Polish first was sent back before the reviewer saw it.
        prompts = self._work_prompts(models)
        self.assertTrue(any("is polish, and the core promises P1 do not hold yet" in one
                            for one in prompts))
        # Both agents are shown the promise; the reviewer is told to try it.
        reviews = [one for one in models.asked if "You review a work agent" in one.system]
        self.assertTrue(any("CORE PROMISES" in _json.dumps(one.messages, default=str)
                            for one in reviews))
        # "Complete" with the promise broken was not taken: it took two rounds.
        self.assertEqual(models.reviewed_rounds, 2)
        self.assertTrue(any(entry.get("what") == "not complete" for entry in run["log"]))
        self.assertEqual(plan["promise_status"]["P1"]["holds"], "yes")
        # Before the final review, a tester used it: told the user's words
        # and the promises - not what the builder claims of it.
        tester = [one for one in models.asked if "You TEST a piece of work" in one.system]
        self.assertEqual(len(tester), 1)
        told = tester[0].messages[0]["content"]
        self.assertIn("write the notes", told)
        self.assertIn("a reader opening notes.md is greeted", told)
        self.assertNotIn("wrote notes.md", told)
        self.assertEqual(plan["black_box"]["promises"][0]["holds"], "yes")
        final = next(one for one in reviews if "THE GOAL IS MET" in str(one.messages))
        self.assertIn("A TESTER WHO NEVER SAW HOW IT WAS BUILT", str(final.messages))
        # The reviewer and the tester run code, and write no file with a tool.
        for request in [*tester, final]:
            names = {spec.name for spec in request.tools or []}
            self.assertIn("run_python", names)
            self.assertNotIn("write_file", names)

    def test_what_only_the_user_can_check_is_listed_and_does_not_hold_it_open(self) -> None:
        models = _OnlyTheUser()
        run, started = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        # Done after one round: the promise is the user's to check.
        self.assertEqual(models.reviewed_rounds, 1)
        plan = run["plan"]
        self.assertEqual(plan["promise_status"]["P1"]["holds"], "needs_user")
        # Once on the list, however many times it is said; with its milestone.
        listed = plan["for_user"]
        self.assertEqual([one["do"] for one in listed],
                         ["make one real pledge of $1 and see it capped"])
        self.assertEqual((listed[0]["promise"], listed[0]["milestone"]), ("P1", "round 1"))
        self.assertEqual(listed[0]["why"], "the provider's test mode passed")
        # The milestone's version carries it, and the report ends with it.
        versions = self.app.work.versions(run["id"])["versions"]
        self.assertTrue(any(one["summary"].get("for_user") for one in versions))
        report = (Path(started["folder"]) / "runs" / run["id"] / "report.md").read_text(
            encoding="utf-8")
        self.assertIn("## For you to check or do", report)
        self.assertIn("make one real pledge of $1 and see it capped (P1) - from round 1", report)
        # Each review was told to keep the list short.
        reviews = [str(one.messages) for one in models.asked
                   if "You review a work agent" in one.system]
        self.assertTrue(all("AS LITTLE AS YOU CAN" in one for one in reviews if
                            "REVIEW THIS ROUND" in one or "THE GOAL IS MET" in one))

    def test_a_promise_the_tester_still_finds_broken_leaves_the_run_unfinished(self) -> None:
        from personalized_agent.agent.work.runner import FIX_ROUNDS, STALLED
        models = _NeverHolds()
        run, _started = self._run(models)
        # Tried again, afresh, after each round of fixes - and not done.
        self.assertEqual(models.tests, FIX_ROUNDS + 1)
        self.assertEqual(run["plan"]["fix_rounds"], FIX_ROUNDS)
        self.assertEqual(run["status"], STALLED, run["log"][-6:])
        self.assertIn("P1 still do not hold", run["final"]["reason"])

    def test_a_promise_never_tried_is_not_done_however_clean_the_review(self) -> None:
        """Seen with a client whose tester could not open the file: the final
        review found nothing to fix, and the run ended done with P1 untried."""
        from personalized_agent.agent.work.runner import FIX_ROUNDS, STALLED
        models = _NeverTried()
        run, _started = self._run(models)
        self.assertEqual(models.tests, FIX_ROUNDS + 1)
        self.assertEqual(run["status"], STALLED, run["log"][-6:])
        self.assertIn("P1 never tried for real", run["final"]["reason"])
        said = [one["say"] for one in run["plan"]["final_review"]["to_fix"]]
        self.assertTrue(any("P1 was never tried for real" in one for one in said))

    def test_a_promise_tried_after_a_fix_round_is_done(self) -> None:
        models = _NeverTried(tried_from=2)
        run, _started = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(models.tests, 2)
        self.assertEqual(run["plan"]["fix_rounds"], 1)
        self.assertEqual(run["plan"]["promise_status"]["P1"]["holds"], "yes")

    def test_the_reviewer_and_the_tester_write_only_in_the_checks_folder(self) -> None:
        from personalized_agent.agent.work.runner import Run
        from personalized_agent.runtime.permissions import SecurityViolation
        unit = {"id": "unit_checks", "name": "Checked work", "kind": "task"}
        folder = self.app.work.folder_of(unit)
        run = Run.__new__(Run)
        run.runtime = self.app.work._runtime(unit, folder)
        run.folder, run.id = Path(run.runtime.policy.write_root), "run_checks"
        run.runtime.write_text(str(run.folder / "product.py"), "print('hi')")
        checking = run._checking_runtime()
        self.assertEqual(Path(checking.policy.write_root).resolve(),
                         run.checks_folder.resolve())
        self.assertEqual(checking.policy.extra_writable, [])
        # The work is read, not written - by a tool or by code it runs.
        self.assertIn("hi", checking.read_text(str(run.folder / "product.py")))
        with self.assertRaises(SecurityViolation):
            checking.write_text(str(run.folder / "product.py"), "changed")
        target = (run.folder / "made_by_check.txt").as_posix()
        checking.run_python_code(f"open({target!r}, 'w').write('x')")
        self.assertFalse((run.folder / "made_by_check.txt").exists())
        self.assertEqual((run.folder / "product.py").read_text(), "print('hi')")
        checking.write_text(str(run.checks_folder / "note.txt"), "ok")
        self.assertTrue((run.checks_folder / "note.txt").exists())

    def test_new_work_needs_a_starting_instruction(self) -> None:
        said = self.app.work.start(self.unit, "  ")
        self.assertIn("new work", said["error"])

    def test_a_run_goes_from_contract_to_deck(self) -> None:
        run, started = self._run(_TwoAgents())
        self.assertEqual(run["status"], "done", run["log"][-6:])
        folder = Path(started["folder"])
        self.assertEqual((folder / "notes.md").read_text(), "hello")
        tree = self.app.work.tree(self.unit)
        self.assertEqual([c["title"] for c in tree["cards"]][1:], ["The notes"])
        notes = tree["cards"][1]
        self.assertEqual(notes["status"], "done")
        # Its criteria and approach, written by the reviewer.
        self.assertEqual([one["text"] for one in notes["checked_by"]["criteria"]],
                         ["notes.md says hello"])
        self.assertEqual(notes["checked_by"]["approach"], "write it plainly")
        # The round, and the reviewer's verdict on it as a stage of its own.
        self.assertEqual([s["kind"] for s in tree["stages"]],
                         ["goal", "plan", "execution", "evaluation"])
        round_stage, verdict = tree["stages"][2], tree["stages"][3]
        self.assertEqual(verdict["notes"]["review_of"], round_stage["id"])
        self.assertTrue(verdict["summary"].startswith("Review of round 1: advanced"))
        from personalized_agent.heartbeat.experience.store import ExperienceStore
        from personalized_agent.agent.work.runner import work_scope
        edges = {(e["source"], e["target"], e["kind"]) for e in
                 ExperienceStore(self.app.db, work_scope(self.unit["id"])).edges()}
        self.assertIn((verdict["id"], round_stage["id"], "evaluates"), edges)
        self.assertEqual(tree["stages"][0]["work"]["decision"]["by"], "user")
        plan = run["plan"]
        self.assertEqual(plan["contract"]["goal"], "notes.md says hello")
        self.assertEqual(plan["decisions"][0]["support"], "some")
        run_dir = folder / "runs" / run["id"]
        for name in ("contract.json", "presentation.html", "report.md"):
            self.assertTrue((run_dir / name).is_file(), name)
        self.assertFalse((run_dir / "todo.md").exists(), "a finished run has no to-do")
        self.assertTrue(plan["deck"]["finished"])
        self.assertEqual(plan["deck"]["deliverables"][0]["location"], "notes.md")
        self.assertIn("greeting", (run_dir / "presentation.html").read_text(encoding="utf-8"))

    def test_a_summary_failing_the_checks_goes_back_unreviewed(self) -> None:
        models = _TwoAgents(gate_fail_first=True)
        run, _started = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        prompts = self._work_prompts(models)
        self.assertTrue(any("FAILED THE CHECKS" in p and "missing.md" in p for p in prompts))
        self.assertEqual(models.reviewed_rounds, 1, "the failing summary was not reviewed")

    def test_a_queued_finding_becomes_a_card_with_its_criteria(self) -> None:
        models = _TwoAgents(queued_once=True, rounds_to_complete=2)
        run, _started = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        cards = {c["title"]: c for c in self.app.work.tree(self.unit)["cards"]}
        self.assertIn("A summary", cards)
        self.assertEqual([one["text"] for one in
                          cards["A summary"]["checked_by"]["criteria"]],
                         ["the summary is one line"])

    def test_three_stalls_end_the_run_and_it_can_be_continued(self) -> None:
        from personalized_agent.agent.work.runner import STALLED
        models = _TwoAgents(stall_always=True)
        run, started = self._run(models)
        self.assertEqual(run["status"], STALLED)
        self.assertEqual(len(run["plan"]["rounds"]), 3)
        self.assertTrue(run["continuable"])
        prompts = self._work_prompts(models)
        self.assertTrue(any("TWO ROUNDS HAVE NOT MOVED THE WORK" in p for p in prompts))
        todo = Path(started["folder"]) / "runs" / run["id"] / "todo.md"
        self.assertIn("nothing changed", todo.read_text(encoding="utf-8"))

    def test_a_card_that_is_blocked_is_set_aside(self) -> None:
        from personalized_agent.agent.work.runner import BLOCKED
        run, _started = self._run(_TwoAgents(blocked=True))
        self.assertEqual(run["status"], BLOCKED, run["log"][-6:])
        notes = self.app.work.tree(self.unit)["cards"][1]
        self.assertEqual(notes["status"], "set_aside")
        self.assertIn("mail server", run["plan"]["blocked"][0]["missing"])

    def test_the_final_review_is_fixed_before_it_is_done(self) -> None:
        models = _TwoAgents(final_finding_once=True)
        run, _started = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(models.final_reviews, 2)
        self.assertEqual(run["plan"]["fix_rounds"], 1)
        prompts = self._work_prompts(models)
        self.assertTrue(any("THE FINAL REVIEW FOUND THESE" in p and "no title" in p
                            for p in prompts))

    def test_the_work_agent_never_sees_the_users_experience(self) -> None:
        from personalized_agent.heartbeat.experience.store import ExperienceStore
        past = ExperienceStore(self.app.db).add_stage({
            "task_id": "task-past", "kind": "plan",
            "summary": "planned how to write the notes, ZEBRAQUOKKA style", "states": [],
            "began": "2026-01-01T00:00:00", "ended": "2026-01-01T00:00:00"})
        models = _TwoAgents()
        self._run(models)
        import json as _json
        reviewer = [_json.dumps(r.messages, default=str) for r in models.asked
                    if "You review a work agent" in r.system]
        self.assertTrue(any("ZEBRAQUOKKA" in one for one in reviewer),
                        "the reviewer should have been shown the past stage")
        for prompt in self._work_prompts(models):
            self.assertNotIn("ZEBRAQUOKKA", prompt)
            self.assertNotIn(past["id"], prompt)

    def test_a_limit_pauses_the_run_and_continue_carries_on(self) -> None:
        from personalized_agent.agent.work.runner import NOT_FINISHED
        models = _TwoAgents()
        # Nine calls, some kept back for the wrap-up: the contract takes its
        # calls (the version it is saved as has no slides now) - its
        # reviewer's what-it-must-know among them - the words its reviewer
        # searches the user's record by one, the round's tool one - and the
        # round is cut off there.
        run, started = self._run(models, limits={"calls": 9, "minutes": 0, "tokens": 0})
        self.assertEqual(run["status"], NOT_FINISHED, run["log"][-6:])
        self.assertTrue(run["continuable"])
        prompts = self._work_prompts(models)
        self.assertTrue(any("THE RUN IS PAUSING HERE" in p for p in prompts))
        plan = run["plan"]
        self.assertEqual(plan["interrupted"]["card"], "The notes")
        run_dir = Path(started["folder"]) / "runs" / run["id"]
        self.assertIn("cut off", (run_dir / "todo.md").read_text(encoding="utf-8"))
        self.assertFalse(plan["deck"]["finished"], "the deck says it is not finished")
        # Continued, with no limits: from where it stopped, not from the start.
        said = self.app.work.continue_run(run["id"], self.unit,
                                          {"calls": 0, "minutes": 0, "tokens": 0})
        self.assertNotIn("error", said, said)
        run = self._wait()
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(len(run["plan"]["sessions"]), 2)
        contracts = [p for p in self._work_prompts(models) if "DRAFT THE CONTRACT" in p]
        self.assertEqual(len(contracts), 1, "the contract is not drafted again")
        self.assertTrue(any("THE RUN WAS PAUSED; ITS TO-DO LIST" in p
                            for p in self._work_prompts(models)))

    def test_the_deck_is_served_and_only_the_runs_own_files(self) -> None:
        from personalized_agent.api.errors import ApiError, Raw
        from personalized_agent.api.routes import build_routes
        run, started = self._run(_TwoAgents())
        routes = build_routes(self.app)

        class Request:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)

            def require(self, key):
                return self.body[key]

        page = routes[("GET", "/api/work/presentation")](Request({"run": run["id"]}))
        self.assertIsInstance(page, Raw)
        self.assertIn("text/html", page.content_type)
        text = page.body.decode("utf-8")
        self.assertIn("Key deliverables", text)
        self.assertIn("notes.md line 1", text)
        figure = Path(started["folder"]) / "plot.png"
        figure.write_bytes(b"\x89PNG\r\n")
        served = routes[("GET", "/api/work/file")](Request({"run": run["id"],
                                                            "path": str(figure)}))
        self.assertEqual(served.content_type, "image/png")
        outside = Path(self.agent.dir) / "elsewhere.png"
        outside.write_bytes(b"\x89PNG\r\n")
        with self.assertRaises(ApiError):
            routes[("GET", "/api/work/file")](Request({"run": run["id"],
                                                       "path": str(outside)}))
        # A finished run is not continued.
        with self.assertRaises(ApiError):
            routes[("POST", "/api/work/continue")](Request({"run": run["id"]}))

    def test_what_it_did_is_recorded_as_the_agents(self) -> None:
        self._run(_TwoAgents())
        events = [e for e in self.app.raw_store.recent_events(limit=100)
                  if (e.data or {}).get("by") == "agent"]
        self.assertTrue(events)
        self.assertTrue(all(e.source == "agent" for e in events))
        self.assertTrue(any(str(e.data.get("path", "")).endswith("notes.md")
                            for e in events))

    def test_limits_as_given(self) -> None:
        from personalized_agent.agent.work.runner import limits_of
        # Zero or less is no limit; what is not given is the default - no
        # limit, by the user's decision - tokens in and out limited apart.
        self.assertEqual(limits_of({"minutes": 0, "tokens_in": -5, "tokens_out": 0,
                                    "calls": 10 ** 9}),
                         {"minutes": 0, "tokens_in": 0, "tokens_out": 0, "calls": 10 ** 9})
        self.assertEqual(limits_of({"tokens_in": "x"}),
                         {"minutes": 0, "tokens_in": 0, "tokens_out": 0, "calls": 0})
        self.assertEqual(limits_of({"tokens_out": 1000}),
                         {"minutes": 0, "tokens_in": 0, "tokens_out": 1000, "calls": 0})

    def test_one_limit_on_tokens_as_before_is_honoured(self) -> None:
        """A caller of before sends one limit on tokens in and out together:
        it is kept, and nothing limits either alone that it did not give."""
        from personalized_agent.agent.work.runner import Run, limits_of
        self.assertEqual(limits_of({"minutes": 0, "tokens": -5, "calls": 10 ** 9}),
                         {"minutes": 0, "tokens_in": 0, "tokens_out": 0, "calls": 10 ** 9})
        self.assertEqual(limits_of({"tokens": 500_000}),
                         {"minutes": 0, "tokens_in": 0, "tokens_out": 0, "calls": 0,
                          "tokens": 500_000})
        self.assertEqual(limits_of({"tokens": 500_000, "tokens_out": 9}),
                         {"minutes": 0, "tokens_in": 0, "tokens_out": 9,
                          "calls": 0, "tokens": 500_000})
        run = Run.__new__(Run)
        run.stopping, run.stop_now, run.stop_reason = False, False, ""
        run.began = __import__("time").time()
        run.limits = limits_of({"tokens": 1_000_000, "minutes": 0, "calls": 0})
        run.used = {"calls": 3, "tokens_in": 600_000, "tokens_out": 300_000}
        self.assertTrue(run._going(work=False))
        self.assertFalse(run._going(work=True))
        self.assertEqual(run.stop_reason, "the token limit")


class TheContractIsLockedTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_no_tool_writes_or_deletes_it(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        folder = self.app.work.folder_for("Locked", "task-lock01")
        runtime = SandboxRuntime(self.app.work.policy_for(folder),
                                 platform=self.app.platform)
        tools = WorkTools(runtime)
        contract = folder / "runs" / "run_1" / "contract.json"
        contract.parent.mkdir(parents=True)
        contract.write_text("{}")
        tools.locked = [contract]
        self.assertIn("locked", tools.call("write_file", {
            "path": "runs/run_1/contract.json", "content": "x"})["error"])
        self.assertIn("locked", tools.call("delete_file", {"path": "runs/run_1"})["error"])
        self.assertEqual(contract.read_text(), "{}")


class ARunGoingWhenTheAppClosedTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_it_is_marked_interrupted_and_can_be_continued(self) -> None:
        from personalized_agent.agent.work.runner import INTERRUPTED, WorkRuns
        self.app.db.execute(
            "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, "
            "started_at, updated_at) VALUES ('run_old', 'task-1', 'x', '', "
            "'running', '2026-10-01', '2026-10-01')")
        again = WorkRuns(self.app.db, self.app.policy, self.app.models, None)
        run = again.runs("task-1")[0]
        self.assertEqual(run["status"], INTERRUPTED)
        self.assertTrue(run["continuable"])


class ANewProjectForTheAgentsTests(unittest.TestCase):
    """Made in the Experience panel: a name, what is wanted, the folders they
    may change, the limits - and the work starts."""

    def setUp(self) -> None:
        import tempfile
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.folder = Path(tempfile.mkdtemp(prefix="pa-new-work-"))
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.folder, ignore_errors=True)
        self.agent.cleanup()

    def _post(self, body):
        class Request:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)

            def require(self, key):
                return self.body[key]

            def int_param(self, key, default):
                return int(self.body.get(key, default))

        return self.routes[("POST", "/api/work/projects")](Request(body))

    def test_it_needs_a_name_and_what_is_wanted(self) -> None:
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            self._post({"name": "", "instruction": "write it"})
        with self.assertRaises(ApiError):
            self._post({"name": "Notes", "instruction": "  "})

    def test_it_is_made_and_the_work_starts(self) -> None:
        import time
        self.app.work.models = _TwoAgents()
        said = self._post({"name": "Notes", "instruction": "write the notes",
                           "folders": [str(self.folder)],
                           "limits": {"minutes": 0, "calls": 50}})
        unit = said["unit"]
        self.assertTrue(said["run"])
        self.assertEqual(said["limits"], {"minutes": 0, "tokens_in": 0,
                                          "tokens_out": 0, "calls": 50})
        self.assertEqual([Path(one) for one in self.app.work.folders(unit)],
                         [self.folder.resolve()])
        experience = self.app.state_heartbeat.experience
        listed = {one["id"]: one for one in experience.units()}
        self.assertEqual(listed[unit]["name"], "Notes")
        # Not one of the user's own folder projects, and never read as one.
        self.assertNotIn(unit, [one["unit_id"] for one in
                                experience.store.workspaces(origin="chosen")])
        self.assertEqual(experience.workspace_of(experience.full_unit(unit),
                                                 experience.stages_writer()), (None, None))
        for _ in range(300):
            runs = self.app.work.runs(unit)
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(self.app.work.runs(unit)[0]["status"], "done")


class WhatEachAgentCanSearchTests(unittest.TestCase):
    """The reviewer searches the stages of all the user's projects; the work
    agent only its own project's."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_reviewer_sees_every_project_and_the_agent_its_own(self) -> None:
        from personalized_agent.agent.work.runner import Run
        from personalized_agent.heartbeat.experience.retrieve import Retriever
        from personalized_agent.heartbeat.experience.store import ExperienceStore
        store = ExperienceStore(self.app.db)
        mine = store.add_stage({"task_id": "task-mine", "kind": "evaluation",
                                "summary": "checked the figure", "states": [],
                                "began": "1", "ended": "1"})
        theirs = store.add_stage({"task_id": "task-other", "kind": "evaluation",
                                  "summary": "checked the figure", "states": [],
                                  "began": "2", "ended": "2"})
        import time
        from types import SimpleNamespace
        run = Run.__new__(Run)
        run.retriever = Retriever(self.app.db, self.app.library)
        # Its project, as the record has it: its tasks are the run's own.
        run._live_unit = {"id": "unit-mine", "tasks": [SimpleNamespace(id="task-mine")]}
        run._live_at = time.time()
        self.assertEqual(run.project_tasks, ["task-mine"])

        def found(said):
            return {step["id"] for step in said["stages"]}

        self.assertEqual(found(run._find_in_project("checked the figure", "")),
                         {mine["id"]})
        self.assertEqual(found(run._find_anywhere("checked the figure", "")),
                         {mine["id"], theirs["id"]})
        # A line in the index opened: its key information, by its id.
        store.add_edge(theirs["id"], mine["id"], "continues")
        opened = run._open_stages([theirs["id"], "stage_nothing"])["stages"]
        self.assertEqual(opened[0]["summary"], "checked the figure")
        self.assertEqual(opened[0]["linked"], [{"to": mine["id"], "kind": "continues",
                                                "direction": "from it"}])
        self.assertEqual(opened[1], {"id": "stage_nothing", "error": "no such stage"})


class TheRecordIsSearchedInEnglishTests(unittest.TestCase):

    def test_the_words_are_asked_for_once(self) -> None:
        from personalized_agent.agent.work.runner import SEARCH_WORDS, Run
        run = Run.__new__(Run)
        run.review_tools = None
        asked = []

        def ask(system, messages, tools, **kwargs):
            asked.append((system, messages[0]["content"]))
            return {"keywords": ["booking page", "payment errors", " refunds "]}

        run._ask = ask
        said = run._in_english({"kind": "plan", "text": "做一个预约页面"})
        self.assertEqual(said["text"], "booking page payment errors refunds")
        self.assertEqual(said["said_as"], "做一个预约页面")
        # Nothing held against what other cards were given: each is ranked on
        # its own.
        self.assertNotIn("seen", said)
        self.assertTrue(asked[0][0].startswith(SEARCH_WORDS))
        run._in_english({"kind": "evaluation", "text": "做一个预约页面"})
        self.assertEqual(len(asked), 1, "asked once for the same words")
        # Nothing to say it with: the words as they were.
        run._ask = lambda *a, **k: {}
        self.assertEqual(run._in_english({"text": "other"})["text"], "other")


class TheRoundsAreLinkedAsTheWorkWentTests(unittest.TestCase):
    """The reviewer's verdict evaluates its round; the next round on the
    card revises what it said; a round whose evidence another card's round
    wrote depends on that round."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_evaluates_revises_and_depends_on(self) -> None:
        import tempfile
        from personalized_agent.agent.work.runner import Run
        from personalized_agent.heartbeat.experience.store import ExperienceStore
        store = ExperienceStore(self.app.db, "work:test")
        run = Run.__new__(Run)
        run.store, run.unit, run.id, run.plan = store, {"id": "unit"}, "run_1", {}
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        run.folder = folder
        a, b = {"id": "card_a", "type": "code"}, {"id": "card_b", "type": "code"}

        def stage(card, n):
            return store.add_stage({"task_id": "unit", "card_id": card["id"],
                                    "kind": "execution", "summary": f"round {n}",
                                    "states": [], "began": str(n), "ended": str(n)})

        first = stage(a, 1)
        run._round_writes = {str(folder / "pledge.py")}
        run._link_round(a, 1, first, ["pledge.py"],
                        {"progress": "advanced", "findings": [{"say": "no cap"}]}, "1")
        second = stage(b, 2)
        run._round_writes = set()
        run._link_round(b, 2, second, ["pledge.py"], {}, "2")
        third = stage(a, 3)
        run._round_writes = set()
        run._link_round(a, 3, third, [], {}, "3")
        verdict = run.plan["review_stages"]["card_a"]
        edges = {(e["source"], e["target"], e["kind"]) for e in store.edges()}
        self.assertEqual(edges, {(verdict, first["id"], "evaluates"),
                                 (second["id"], first["id"], "depends_on"),
                                 (third["id"], verdict, "revises")})

class ALimitOfZeroIsNoLimitTests(unittest.TestCase):

    def test_nothing_stops_a_run_that_has_none(self) -> None:
        from personalized_agent.agent.work.runner import Run
        run = Run.__new__(Run)
        run.stopping, run.stop_now, run.stop_reason, run.began = False, False, "", 0.0
        run.limits = {"minutes": 0, "tokens": 0, "calls": 0}
        run.used = {"calls": 10 ** 6, "tokens_in": 10 ** 9, "tokens_out": 0}
        self.assertTrue(run._going())
        run.limits["calls"] = 5
        self.assertFalse(run._going())
        self.assertEqual(run.stop_reason, "the call limit")

    def test_tokens_in_and_out_stop_a_run_each_at_its_own(self) -> None:
        """What went in and what came out are limited apart: each limit
        stops the run when it is reached, the other far from its own - and
        each keeps its reserve for the wrap-up."""
        from personalized_agent.agent.work.runner import Run
        run = Run.__new__(Run)
        run.stopping, run.stop_now, run.stop_reason = False, False, ""
        run.began = __import__("time").time()
        run.limits = {"minutes": 0, "tokens_in": 1_000_000, "tokens_out": 100_000, "calls": 0}
        # The input's reserve: 400,000 of the million (its floor).
        run.used = {"calls": 1, "tokens_in": 550_000, "tokens_out": 1_000}
        self.assertTrue(run._going(work=True))
        run.used["tokens_in"] = 650_000           # inside the reserve: the wrap-up may go on
        self.assertFalse(run._going(work=True))
        self.assertEqual(run.stop_reason, "the input token limit")
        self.assertTrue(run._going(work=False))
        run.stop_reason = ""
        run.used = {"calls": 1, "tokens_in": 10, "tokens_out": 100_000}
        self.assertFalse(run._going(work=False))
        self.assertEqual(run.stop_reason, "the output token limit")

    def test_the_reserve_is_kept_for_the_wrap_up(self) -> None:
        """New work stops while some of the limit is left; the wrap-up may
        spend it - the deck and the to-do list are not rushed out."""
        from personalized_agent.agent.work.runner import Run
        run = Run.__new__(Run)
        run.stopping, run.stop_now, run.stop_reason = False, False, ""
        run.began = __import__("time").time()
        run.limits = {"minutes": 0, "tokens": 0, "calls": 100}
        run.used = {"calls": 91, "tokens_in": 0, "tokens_out": 0}
        self.assertFalse(run._going(work=True))
        self.assertTrue(run._going(work=False))
        run.used["calls"] = 100
        self.assertFalse(run._going(work=False))
        # Stopping ends the work and leaves the wrap-up; stopping now, both.
        run.used["calls"], run.stop_reason = 0, ""
        run.stopping = True
        self.assertFalse(run._going(work=True))
        self.assertTrue(run._going(work=False))
        run.stop_now = True
        self.assertFalse(run._going(work=False))


class TheAgentsOwnActivityIsNotTheUsersTests(unittest.TestCase):
    """A state the agent heartbeat writes from the agent's own work run is
    marked as the agent's, and the state heartbeat does not read it."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_state_resting_on_the_agents_events_is_the_agents(self) -> None:
        from personalized_agent.common.events.models import ListenerEvent
        mine = self.app.raw_store.add_event(ListenerEvent(
            event_type="agent.work.wrote", source="agent",
            data={"by": "agent", "run": "run_1", "path": "C:/w/notes.md"}))
        theirs = self.app.raw_store.add_event(ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            data={"path": "C:/w/other.md"}))
        beat = self.app.heartbeat
        self.assertTrue(beat._the_agents({"materials": [mine.id]}))
        self.assertFalse(beat._the_agents({"materials": [mine.id, theirs.id]}))
        self.assertTrue(beat._the_agents({"whose": "agent", "materials": []}))

    def test_the_state_heartbeat_reads_only_the_users_states(self) -> None:
        from personalized_agent.agent.states.store import ABOUT_AGENT, State
        user = self.app.states.add(State(title="The user wrote the paper.", detail="d"))
        agents = self.app.states.add(State(title="The agent worked on the paper.",
                                           detail="d", about=ABOUT_AGENT))
        ids = [s.id for s in self.app.states.recent(limit=50, users_only=True)]
        self.assertIn(user.id, ids)
        self.assertNotIn(agents.id, ids)

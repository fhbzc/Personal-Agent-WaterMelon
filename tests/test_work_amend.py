"""New words when a work run is continued: an AMENDED CONTRACT for that line.

The work agent drafts the amendment from the contract as it stands and the
user's words, verbatim - every clause the words do not touch kept word for
word - and the reviewer checks it and approves it or sends it back, never
asking the user. The words are kept in the contract as said, both agents
read them for the rest of the line, what changed is shown clause by clause,
and done work that no longer holds is opened again. The other line keeps
its own contract."""

from __future__ import annotations

import json
import re
import unittest

from test_work_runs import _TwoAgents
from test_work_versions import _Request, _RunsBase

WORDS = ("Also put a title line at the top of notes.md - the style is at "
         "https://style.example.org/guide/titles\nKeep everything else as it is.")


class _Continues(_TwoAgents):
    """The two agents, able to go on from a version where the notes are
    done already: a done card is found as the tree shows it too."""

    @staticmethod
    def _card(text, title):
        found = _TwoAgents._card(text, title) or re.search(
            r"\[(card_[0-9a-zA-Z]+)\][^\n]*?: " + re.escape(title), text)
        return found if isinstance(found, str) else found.group(1) if found else ""


class _Amends(_Continues):
    """The two agents, who also amend the contract: the work agent adds a
    deliverable and a line of scope and keeps the rest word for word; the
    reviewer approves - after sending the first draft back, if asked to -
    writes the new deliverable's criteria, and may open the notes again."""

    def __init__(self, reject_first=False, reopen=False, **kwargs):
        super().__init__(**kwargs)
        self.reject_first = reject_first
        self.reopen = reopen
        self.drafts = 0
        self.amend_reviews = 0

    def invoke(self, request, role=""):
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else json.dumps(last)
        if "You review a work agent" in request.system and "REVIEW THIS AMENDMENT" in said:
            self.asked.append(request)
            self.amend_reviews += 1
            if self.reject_first and self.amend_reviews == 1:
                return self._reply(json.dumps({
                    "verdict": "revise", "comments": [{"say": "the title is not in scope"}],
                    "change": "add the title to the scope"}))
            notes = self._card(said, "The notes")
            return self._reply(json.dumps({
                "verdict": "approve", "comments": [{"say": "it follows the words"}],
                "deliverables": [{"ref": "d2", "criteria": [
                    {"text": "the first line of notes.md is a title", "backed_by": "check"}],
                    "approach": "one short line"}],
                "reopen": [{"card": notes, "why": "it has no title yet"}]
                if self.reopen else []}))
        if "You review a work agent" not in request.system \
                and "AMEND THE CONTRACT FOR THIS BRANCH" in first:
            self.asked.append(request)
            self.drafts += 1
            ref = re.search(r'"ref": "(card_[0-9a-zA-Z]+)"', first).group(1)
            scope = ["the notes"]
            if not self.reject_first or self.drafts > 1:
                scope.append("a title line at the top of the notes")
            return self._reply(json.dumps({
                "goal": "notes.md says hello", "scope": scope,
                "out_of_scope": ["anything else"],
                "deliverables": [
                    {"ref": ref, "title": "The notes", "type": "document",
                     "statement": "notes.md says hello"},
                    {"ref": "d2", "title": "The title", "type": "document",
                     "statement": "notes.md starts with a title line", "why": "asked"}],
                "changed": ["added the title"]}))
        return super().invoke(request, role)


class _Counts(_Continues):
    """The two agents, counting any amendment they are asked for."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.amendments = 0

    def invoke(self, request, role=""):
        text = json.dumps(request.messages, default=str)
        if "AMEND THE CONTRACT" in text or "REVIEW THIS AMENDMENT" in text:
            self.amendments += 1
        return super().invoke(request, role)


class _AmendBase(_RunsBase):

    def _continue_with(self, run_id, models, words, from_version=""):
        self.app.work.models = models
        said = self.app.work.continue_run(run_id, self.unit,
                                          {"calls": 0, "minutes": 0, "tokens": 0},
                                          from_version=from_version, instruction=words)
        self.assertNotIn("error", said, said)
        return said, self._wait(run_id)

    def _state(self, folder, run_id, version_id):
        from personalized_agent.agent.work.versions import VersionStore
        store = VersionStore(folder / "runs" / run_id)
        return store, store.get_json(store.manifest(version_id)["state"])

    def _card_in(self, store, state, title):
        for sha in state["tree"]["experience_cards"]:
            row = store.get_json(sha)
            if row.get("title") == title:
                return row
        return None


class AnAmendmentForTheBranchTests(_AmendBase):

    def test_continuing_from_v2_with_words_amends_that_line_only(self) -> None:
        run, folder = self._run(_TwoAgents())
        old = run["plan"]["contract"]
        models = _Amends()
        said, after = self._continue_with(run["id"], models, WORDS, from_version="v2")
        self.assertTrue(said["amending"])
        self.assertEqual(after["status"], "done", after["log"][-6:])
        # One draft, one review: the first draft was approved.
        self.assertEqual((models.drafts, models.amend_reviews), (1, 1))
        # Only what the words touch changed; the rest is word for word.
        new = after["plan"]["contract"]
        self.assertEqual(new["goal"], old["goal"])
        self.assertEqual(new["out_of_scope"], old["out_of_scope"])
        self.assertEqual(new["scope"], old["scope"] + ["a title line at the top of the notes"])
        self.assertEqual(new["deliverables"][0], old["deliverables"][0])
        self.assertEqual([one["title"] for one in new["deliverables"]],
                         ["The notes", "The title"])
        self.assertTrue(new["deliverables"][1]["card"].startswith("card_"))
        # The version it is saved as, named for what it added - its body
        # what changed, clause by clause.
        version = [one for one in self.app.work.versions(run["id"])["versions"]
                   if one["kind"] == "amended"][0]
        self.assertEqual(version["message"]["title"], "Amend contract: add The title")
        self.assertIn("+ DELIVERABLE: The title - notes.md starts with a title line",
                      version["message"]["body"])
        # The user's words, kept in it verbatim.
        self.assertEqual(new["instructions"], [WORDS])
        written = json.loads((folder / "runs" / run["id"] / "contract.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(written["instructions"], [WORDS])
        # The new deliverable has the criteria the reviewer wrote.
        tree = self.app.work.tree(self.unit)
        title = [c for c in tree["cards"] if c["title"] == "The title"][0]
        self.assertEqual([one["text"] for one in title["checked_by"]["criteria"]],
                         ["the first line of notes.md is a title"])
        # What changed, clause by clause, in the live session.
        events = self.app.work.live(run["id"])["events"]
        amended = [e for e in events if e["kind"] == "contract"]
        self.assertEqual(len(amended), 1)
        self.assertEqual(amended[0]["who"], "run")
        self.assertIn("Contract amended for this branch", amended[0]["text"])
        diff = amended[0]["detail"]["diff"].splitlines()
        self.assertIn("+ IN SCOPE: a title line at the top of the notes", diff)
        self.assertIn("+ DELIVERABLE: The title - notes.md starts with a title line", diff)
        self.assertIn("  GOAL: notes.md says hello", diff)
        self.assertIn("  DELIVERABLE: The notes - notes.md says hello", diff)
        self.assertFalse([one for one in diff if one.startswith("- ")])
        self.assertEqual(amended[0]["detail"]["words"], WORDS)
        self.assertTrue(any(e["who"] == "you" and "title line" in e["text"] for e in events))
        # The tree of versions: the old line as it was, the new line's
        # first version carrying the words and the diff.
        tree = self.routes[("GET", "/api/work/versions")](_Request({"run": run["id"]}))
        by_id = {one["id"]: one for one in tree["versions"]}
        self.assertEqual(by_id["v6"]["kind"], "amended")
        self.assertEqual((by_id["v6"]["parent"], by_id["v6"]["branch"]), ("v2", "branch 2"))
        self.assertEqual(by_id["v6"]["amended"]["words"], [WORDS])
        self.assertIn("+ DELIVERABLE: The title", by_id["v6"]["amended"]["diff"])
        self.assertEqual([one["id"] for one in tree["versions"] if one.get("amended")], ["v6"])
        # v3 is still there, and its saved plan has the contract as it was.
        self.assertIn("v3", by_id)
        _store, v3 = self._state(folder, run["id"], "v3")
        self.assertEqual(v3["plan"]["contract"], old)
        self.assertNotIn("instructions", v3["plan"]["contract"])
        _store, v6 = self._state(folder, run["id"], "v6")
        self.assertEqual(v6["plan"]["contract"]["instructions"], [WORDS])
        # Both agents read the words, verbatim, for the rest of the line.
        rounds = [json.dumps(r.messages) for r in models.asked
                  if "You review a work agent" not in r.system
                  and "WORK ONE ROUND" in json.dumps(r.messages)]
        reviews = [json.dumps(r.messages) for r in models.asked
                   if "You review a work agent" in r.system
                   and "REVIEW THIS ROUND" in json.dumps(r.messages)]
        self.assertTrue(rounds and reviews)
        quoted = json.dumps(WORDS)[1:-1]
        self.assertTrue(all(quoted in one for one in rounds))
        self.assertTrue(all(quoted in one for one in reviews))
        # And going back to the old line, its contract is its own.
        self.app.work.models = _TwoAgents()
        said = self.app.work.continue_run(run["id"], self.unit, {}, from_version="v3")
        self.assertNotIn("error", said, said)
        back = self._wait(run["id"])
        self.assertNotIn("instructions", back["plan"]["contract"])

    def test_the_reviewer_opens_done_work_again(self) -> None:
        run, folder = self._run(_TwoAgents())
        self.assertEqual(run["plan"]["rounds"][0]["progress"], "advanced")
        store, v2 = self._state(folder, run["id"], "v2")
        self.assertEqual(self._card_in(store, v2, "The notes")["status"], "done")
        _said, after = self._continue_with(run["id"], _Amends(reopen=True), WORDS,
                                           from_version="v2")
        # As the amendment left it - the version saved with it: not done.
        amended = [one for one in self._versions(run["id"])["versions"]
                   if one["kind"] == "amended"][0]
        store, state = self._state(folder, run["id"], amended["id"])
        notes = self._card_in(store, state, "The notes")
        self.assertEqual(notes["status"], "open")
        self.assertIn("it has no title yet", notes["status_why"])
        self.assertEqual(amended["amended"]["reopened"], ["The notes"])
        events = [e for e in self.app.work.live(run["id"])["events"] if e["kind"] == "contract"]
        self.assertEqual(events[-1]["detail"]["reopened"], ["The notes"])
        self.assertIn("opened again", events[-1]["text"])
        # The round after it - the second, v2 had one - worked the notes again.
        self.assertEqual([one["card"] for one in after["plan"]["rounds"]],
                         ["The notes", "The notes"])

    def test_reopening_is_not_counted_against_the_natures(self) -> None:
        # It is the reviewer that opens work again - the agents' doing, which
        # never moves how sure a nature is: even an engine handed to the runs
        # is not told.
        run, _folder = self._run(_TwoAgents())
        told = []
        self.app.work.lessons = type("Engine", (), {
            "used": lambda *a, **k: told.append("used") or True,
            "outcome": lambda *a, **k: told.append("outcome") or 1})()
        self._continue_with(run["id"], _Amends(reopen=True), WORDS, from_version="v2")
        self.assertEqual(told, [])

    def test_a_draft_sent_back_is_drafted_again(self) -> None:
        run, _folder = self._run(_TwoAgents())
        models = _Amends(reject_first=True)
        _said, after = self._continue_with(run["id"], models, WORDS, from_version="v2")
        self.assertEqual((models.drafts, models.amend_reviews), (2, 2))
        self.assertEqual(after["plan"]["contract"]["scope"],
                         ["the notes", "a title line at the top of the notes"])
        verdicts = [one.get("verdict") for one in after["log"]
                    if one["what"] == "amendment reviewed"]
        self.assertEqual(verdicts, ["revise", "approve"])

    def test_never_approved_the_contract_stays_and_no_work_is_done(self) -> None:
        run, _folder = self._run(_TwoAgents())

        class _NeverApproves(_Amends):
            def invoke(self, request, role=""):
                said = json.dumps(request.messages[-1]["content"])
                if "You review a work agent" in request.system and \
                        "REVIEW THIS AMENDMENT" in said:
                    self.amend_reviews += 1
                    return self._reply(json.dumps({"verdict": "revise", "change": "no"}))
                return super().invoke(request, role)

        models = _NeverApproves()
        _said, after = self._continue_with(run["id"], models, WORDS, from_version="v2")
        from personalized_agent.agent.work.runner import CONTRACT_REVIEWS, NOT_FINISHED
        self.assertEqual(models.amend_reviews, CONTRACT_REVIEWS)
        self.assertEqual(models.drafts, CONTRACT_REVIEWS)
        self.assertEqual(after["status"], NOT_FINISHED)
        self.assertIn("not approved", after["final"]["reason"])
        self.assertNotIn("instructions", after["plan"]["contract"])
        self.assertEqual(models.reviewed_rounds, 0, "no work was done")


class AFinishedRunGoesOnWithWordsTests(_AmendBase):
    """A finished run is carried on from where it finished - a new task on
    top of what is done - but only with words saying what to do next."""

    def test_finished_with_nothing_said_is_refused(self) -> None:
        run, _folder = self._run(_TwoAgents())
        self.assertEqual(run["status"], "done")
        entry = [one for one in self.app.work.runs(self.unit["id"])
                 if one["id"] == run["id"]][0]
        self.assertTrue(entry["extendable"])
        self.assertFalse(entry["continuable"])
        said = self.app.work.continue_run(run["id"], self.unit, {}, instruction="  ")
        self.assertIn("say what to do next", said["error"])

    def test_finished_with_words_amends_and_works_on_the_same_line(self) -> None:
        run, _folder = self._run(_TwoAgents())
        before = self._versions(run["id"])
        rounds = len(run["plan"]["rounds"])
        models = _Amends()
        said, after = self._continue_with(run["id"], models, WORDS)
        self.assertTrue(said["amending"])
        self.assertNotIn("rolled_back", said)
        self.assertEqual((models.drafts, models.amend_reviews), (1, 1))
        self.assertEqual(after["status"], "done", after["log"][-6:])
        self.assertEqual(after["plan"]["contract"]["instructions"], [WORDS])
        # The work was taken up again, the new deliverable in its contract.
        self.assertGreater(len(after["plan"]["rounds"]), rounds)
        self.assertEqual([one["title"] for one in after["plan"]["contract"]["deliverables"]],
                         ["The notes", "The title"])
        # On the same line, after what was there.
        versions = self._versions(run["id"])
        amended = [one for one in versions["versions"] if one["kind"] == "amended"][0]
        self.assertEqual(amended["parent"], before["head"])
        self.assertEqual(amended["branch"], before["branch"])


class TheContinueRouteTests(_AmendBase):

    def test_the_route_passes_the_words(self) -> None:
        run, _folder = self._run(_TwoAgents())
        seen = {}

        def continue_run(run_id, unit, limits=None, packages=None, from_version="",
                         instruction="", sound=False):
            seen.update(run=run_id, from_version=from_version, instruction=instruction)
            return {"run": run_id}

        self.app.work.continue_run = continue_run
        self.routes[("POST", "/api/work/continue")](_Request({
            "run": run["id"], "from_version": "v2",
            "instruction": "  " + WORDS + " " * 3 + "x" * 5000}))
        self.assertEqual(seen["from_version"], "v2")
        self.assertTrue(seen["instruction"].startswith(WORDS))
        self.assertEqual(len(seen["instruction"]), 4000)

    def test_no_words_is_as_before(self) -> None:
        run, _folder = self._run(_TwoAgents())
        models = _Counts()
        said, after = self._continue_with(run["id"], models, "   ", from_version="v2")
        self.assertNotIn("amending", said)
        self.assertEqual(after["status"], "done")
        self.assertEqual(models.amendments, 0)
        self.assertNotIn("instructions", after["plan"]["contract"])
        new = self._versions(run["id"])["versions"][5:]
        self.assertNotIn("amended", [one["kind"] for one in new])
        self.assertFalse([one for one in new if "amended" in one])
        self.assertFalse([e for e in self.app.work.live(run["id"])["events"]
                          if e["kind"] == "contract"])

    def test_the_addresses_in_the_words_become_known(self) -> None:
        from personalized_agent.agent.work.tools import address_key
        run, _folder = self._run(_TwoAgents())
        self._continue_with(run["id"], _Amends(), WORDS, from_version="v2")
        live = self.app.work._running[run["id"]]
        key = address_key("https://style.example.org/guide/titles")
        self.assertIn(key, live.tools.known_addresses)
        self.assertIn(key, live.review_tools.known_addresses)
        # Continued again on that line without new words: still known - the
        # words are in its contract.
        self.app.work.models = _Fails()
        said = self.app.work.continue_run(run["id"], self.unit, {}, from_version="v6")
        self.assertNotIn("error", said, said)
        self._wait(run["id"])
        again = self.app.work._running[run["id"]]
        self.assertIsNot(again, live)
        self.assertIn(key, again.tools.known_addresses)
        self.assertIn(key, again.review_tools.known_addresses)


class _Fails:
    def invoke(self, request, role=""):
        raise RuntimeError("no model in this test")


class TheContractInForceTests(_AmendBase):
    """What the panel is given: the contract in force - its line, the
    version it holds since, each deliverable's status and criteria, and the
    words it was amended by - and each version's own contract."""

    def test_the_runs_carry_the_contract_in_force(self) -> None:
        run, _folder = self._run(_TwoAgents())
        listed = self.app.work.runs(self.unit["id"])[0]["contract_in_force"]
        self.assertEqual((listed["branch"], listed["since"], listed["since_kind"]),
                         ("main", "v1", "contract"))
        self.assertEqual((listed["done"], listed["total"]), (1, 1))
        notes = listed["deliverables"][0]
        self.assertEqual((notes["title"], notes["status"]), ("The notes", "done"))
        self.assertEqual(notes["criteria"], [{"text": "notes.md says hello",
                                              "backed_by": "check"}])
        self.assertEqual(notes["approach"], "write it plainly")
        self.assertIsNone(listed["amended"])
        # Amended on a branch: in force there, since the amendment, with the
        # words and what changed; the new deliverable with its criteria.
        self._continue_with(run["id"], _Amends(), WORDS, from_version="v2")
        listed = self.app.work.runs(self.unit["id"])[0]["contract_in_force"]
        self.assertEqual((listed["branch"], listed["since"], listed["since_kind"]),
                         ("branch 2", "v6", "amended"))
        self.assertEqual(listed["instructions"], [WORDS])
        self.assertIn("+ DELIVERABLE: The title", listed["amended"]["diff"])
        self.assertEqual([one["title"] for one in listed["deliverables"]],
                         ["The notes", "The title"])
        self.assertEqual(listed["deliverables"][1]["criteria"][0]["text"],
                         "the first line of notes.md is a title")
        self.assertEqual(listed["total"], 2)
        # An older version's contract is its own: v2's, from before.
        old = self.routes[("GET", "/api/work/version")](_Request({"run": run["id"],
                                                                  "id": "v2"}))
        self.assertFalse(old["head"])
        self.assertEqual([one["title"] for one in old["contract"]["deliverables"]],
                         ["The notes"])
        self.assertEqual(old["contract"]["deliverables"][0]["status"], "done")
        self.assertEqual(old["contract"]["instructions"], [])
        self.assertIsNone(old["amended"])
        amended = self.routes[("GET", "/api/work/version")](_Request({"run": run["id"],
                                                                      "id": "v6"}))
        self.assertEqual(amended["contract"]["instructions"], [WORDS])
        self.assertEqual(amended["amended"]["words"], [WORDS])
        # The live session's head has it in a line.
        live = self.app.work.live(run["id"])["contract"]
        self.assertEqual((live["branch"], live["since"], live["total"], live["amended"]),
                         ("branch 2", "v6", 2, True))


class ThePanelTests(unittest.TestCase):
    """The panel: the contract in force on top, unfolded, its criteria one
    click away; a line amended shows its words and what changed; what to
    change is asked under Continue and under the version picked, and posted;
    an older version's contract says it is not in force."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _render(self, stub, steps):
        """The work panel in a browser, over `stub`, after `steps`: `out`."""
        import html
        import subprocess
        import tempfile
        from pathlib import Path
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        harness = (
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>window.__posts=[];window.__gets=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
            "else window.__gets.push(String(u));"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||120));"
            "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=20000",
                 "--window-size=1200,800", "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "", result["err"])
        return result["out"]

    def test_the_contract_in_force_and_what_to_change(self) -> None:
        import html
        import subprocess
        import tempfile
        from pathlib import Path
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        diff = ("  GOAL: notes.md says hello\n+ IN SCOPE: a title line\n"
                "- OUT OF SCOPE: anything else\n  DELIVERABLE: The notes - says hello")
        amended = {"words": [WORDS], "said": WORDS, "diff": diff, "reopened": ["The notes"]}
        criteria = [{"text": "notes.md says hello", "backed_by": "check"}]
        in_force = {"goal": "notes.md says hello", "scope": ["the notes", "a title line"],
                    "out_of_scope": [], "instructions": [WORDS],
                    "deliverables": [{"card": "card_n1", "title": "The notes",
                                      "statement": "notes.md says hello", "status": "open",
                                      "criteria": criteria, "approach": "write it plainly"},
                                     {"card": "card_t1", "title": "The title",
                                      "statement": "a title line", "status": "done",
                                      "criteria": [], "approach": ""}],
                    "done": 1, "total": 2, "branch": "branch 2", "head": "v3",
                    "since": "v3", "since_kind": "amended", "amended": amended}
        run = {"id": "run_2", "folder": "C:/w/agent_work/x", "alive": False,
               "status": "not finished", "continuable": True,
               "started_at": "2026-10-08T12:00:00", "instruction": "write the notes",
               "final": {}, "log": [], "contract_in_force": in_force,
               "plan": {"packages": {"mode": "ask", "requests": []},
                        "contract": {"goal": "notes.md says hello"}},
               "versions": {"count": 3, "head": "v3", "branch": "branch 2"}}

        def version(vid, parent, branch, kind, label, **more):
            return {"id": vid, "n": int(vid[1:]), "parent": parent, "branch": branch,
                    "kind": kind, "label": label, "at": "2026-10-08T12:0%s:00" % vid[1:],
                    "files": 3, "granted": 0, **more,
                    "summary": {"rounds": 1, "cards": 2, "cards_done": 1, "todo": 1,
                                "deck": False, "phase": "rounds", "status": "running"}}
        tree = {"run": "run_2", "head": "v3", "branch": "branch 2",
                "branches": ["main", "branch 2"], "alive": False, "status": "not finished",
                "versions": [version("v1", "", "main", "contract", "The contract approved"),
                             version("v2", "v1", "main", "round", "Round 1"),
                             version("v3", "v1", "branch 2", "amended",
                                     "The contract amended for this branch",
                                     amended=amended)]}
        detail = {"id": "v1", "label": "The contract approved", "kind": "contract",
                  "branch": "main", "parent": "", "at": "2026-10-08T12:01:00",
                  "head": False, "goal": "notes.md says hello", "phase": "rounds",
                  "files": [], "granted": [], "todo": {}, "deck": False, "amended": None,
                  "contract": {"goal": "notes.md says hello", "scope": ["the notes"],
                               "out_of_scope": ["anything else"], "instructions": [],
                               "deliverables": [{"card": "card_n1", "title": "The notes",
                                                 "statement": "notes.md says hello",
                                                 "status": "open", "criteria": criteria,
                                                 "approach": ""}],
                               "done": 0, "total": 1}}
        live = {"run": "run_2", "events": [
                    {"seq": 1, "at": "2026-10-08T12:00:01+00:00", "who": "run",
                     "kind": "contract", "text": "Contract amended for this branch",
                     "detail": {"diff": diff, "words": WORDS, "reopened": []}}],
                "last": 1, "state": "ended", "status": "not finished", "pending": [],
                "requests": {}, "resumes_from": None,
                "contract": {"goal": "notes.md says hello", "branch": "branch 2",
                             "since": "v3", "done": 1, "total": 2, "amended": True},
                "progress": {"stage": "rounds", "phase": "rounds", "round": 1,
                             "rounds_done": 1, "cards": 2, "cards_done": 1,
                             "used": {"minutes": 1, "tokens": 10, "calls": 2},
                             "limits": {}, "activity": "", "pause_asked": False,
                             "held": False, "stopping": False}}
        stub = {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens": 1000000,
                                                  "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/versions": tree, "/api/work/version": detail,
                "/api/work/live": live,
                "/api/work/continue": {"run": "run_2"}}
        steps = (
            "activeTab='experience';"
            "document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.id==='experience'));"
            "document.body.style.minHeight='5000px';"
            "experienceState.unit='task-amend';await xpLoadRuns();await wait();"
            "const shown=(el)=>!!el&&el.checkVisibility()&&!el.closest('details:not([open]) > :not(summary)');"
            "const text=(el)=>el?el.textContent.replace(/\\s+/g,' ').trim():'';"
            "const block=document.querySelector('.xp-run .xp-contract-now');"
            "out.blockFirst=block.closest('.xp-contract-box').previousElementSibling.tagName;"
            "out.blockShown=shown(block);"
            "out.title=text(block.querySelector('.xp-contract-title'));"
            "out.block=text(block);"
            "out.critBefore=shown(block.querySelector('.xp-criteria'));"
            "out.diffFolded=!block.querySelector('.xp-contract-diff').open;"
            "out.diffLines=[...block.querySelectorAll('.xp-diff span')].map(s=>s.className);"
            "block.querySelector('[data-xp-deliverable]').dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "block.querySelector('[data-xp-deliverable]').parentElement.open=true;await wait();"
            "out.critAfter=shown(block.querySelector('.xp-criteria'));"
            "out.crit=text(block.querySelector('.xp-deliverable-body'));"
            # Redrawn, the deliverable opened stays open.
            "await xpLoadRuns();await wait();"
            "out.critKept=shown(document.querySelector('.xp-run .xp-contract-now .xp-criteria'));"
            # The live session's head says the contract in a line.
            "document.querySelector('[data-xp-live-toggle=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "document.querySelector('.xp-live').open=true;await wait(1400);"
            "out.liveHead=text(document.querySelector('.xp-live-contract'));"
            "out.liveDiff=!!document.querySelector('.xp-live-ev .xp-contract-diff');"
            # What to change, under Continue: typed and posted.
            "let box=document.querySelector('.xp-continue-row [data-xp-continue-instruction=\"run_2\"]');"
            "out.rowBox=!!box;out.rowLabel=text(box.closest('label'));"
            "box.value='  Make it shorter.  ';box.dispatchEvent(new Event('input',{bubbles:true}));"
            "document.querySelector('.xp-continue-row [data-xp-continue=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            # The versions: the amended line shows its words and changes.
            "document.querySelector('[data-xp-versions=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "document.querySelector('.xp-versions').open=true;await wait();"
            "const v3=document.querySelector('[data-xp-version-row=\"v3\"]');"
            "out.v3=text(v3.querySelector('.xp-amended'));"
            "out.v3Diff=!!v3.querySelector('.xp-contract-diff .xp-diff-add');"
            "out.v2Amended=!!document.querySelector('[data-xp-version-row=\"v2\"] .xp-amended');"
            # An older version's contract: not in force.
            "document.querySelector('[data-xp-version-view=\"v1\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "const view=document.querySelector('.xp-version-view');"
            "out.oldTitle=text(view.querySelector('.xp-contract-title'));"
            "out.oldMarked=!!view.querySelector('.xp-contract-not-in-force');"
            "out.oldGoal=text(view.querySelector('.xp-contract-now'));"
            # Continue from v1: the words are asked under the tree, where v1
            # is shown - the page still.
            "window.scrollTo(0,400);const y=window.scrollY;"
            "document.querySelector('[data-xp-version-from=\"v1\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "out.pageY=[y,window.scrollY];"
            "box=document.querySelector('.xp-version-picked .xp-version-continue textarea');"
            "out.underV1=!!box;out.focused=document.activeElement===box;"
            "out.rowHasBox=!!document.querySelector('.xp-continue-row textarea');"
            "box.value='Use the old style instead';box.dispatchEvent(new Event('input',{bubbles:true}));"
            "document.querySelector('.xp-version-continue [data-xp-continue=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            # Nothing said: nothing sent.
            "document.querySelector('.xp-continue-row [data-xp-continue=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "out.posts=window.__posts.filter(p=>p.p==='/api/work/continue').map(p=>p.body);")
        out = self._render(stub, steps)
        # On top of the run, right under its summary, and not folded.
        self.assertEqual(out["blockFirst"], "SUMMARY")
        self.assertTrue(out["blockShown"])
        self.assertIn("Contract in force", out["title"])
        self.assertIn("branch 2, since v3", out["title"])
        for said in ("Goal: notes.md says hello", "In scope: the notes; a title line",
                     "1/2 done", "open The notes", "done The title", "1 criterion",
                     "0 criteria", "title line at the top of notes.md"):
            self.assertIn(said, out["block"])
        # The words, verbatim, and what changed folded, +/- marked.
        self.assertTrue(out["diffFolded"])
        self.assertEqual(out["diffLines"], ["xp-diff-same", "xp-diff-add", "xp-diff-del",
                                            "xp-diff-same"])
        # Criteria one click away.
        self.assertFalse(out["critBefore"])
        self.assertTrue(out["critAfter"])
        self.assertIn("notes.md says hello (check)", out["crit"])
        self.assertIn("How to go about it: write it plainly", out["crit"])
        self.assertTrue(out["critKept"])
        # The live session's head, and the amendment's diff in it.
        self.assertIn("contract (amended): notes.md says hello", out["liveHead"])
        self.assertIn("branch 2 · 1/2 deliverables done", out["liveHead"])
        self.assertTrue(out["liveDiff"])
        # What to change: under Continue, and under the version picked.
        self.assertTrue(out["rowBox"])
        self.assertIn("What to change from here (optional)", out["rowLabel"])
        self.assertIn("instruction", out["v3"])
        self.assertIn("title line at the top of notes.md", out["v3"])
        self.assertIn("opened again: The notes", out["v3"])
        self.assertTrue(out["v3Diff"])
        self.assertFalse(out["v2Amended"])
        self.assertEqual(out["oldTitle"], "Contract at v1 - not in force")
        self.assertTrue(out["oldMarked"])
        self.assertIn("Out of scope: anything else", out["oldGoal"])
        self.assertEqual(out["pageY"][0], out["pageY"][1], "the page did not move")
        self.assertTrue(out["underV1"])
        self.assertTrue(out["focused"])
        self.assertFalse(out["rowHasBox"], "one place to say it at a time")
        posts = out["posts"]
        self.assertEqual(len(posts), 3, posts)
        self.assertEqual(posts[0]["instruction"], "Make it shorter.")
        self.assertNotIn("from_version", posts[0])
        self.assertEqual((posts[1]["from_version"], posts[1]["instruction"]),
                         ("v1", "Use the old style instead"))
        self.assertNotIn("instruction", posts[2])

    def test_a_finished_run_asks_what_to_do_next_and_needs_it(self) -> None:
        run = {"id": "run_9", "folder": "C:/w/agent_work/x", "alive": False,
               "status": "done", "continuable": False, "extendable": True,
               "started_at": "2026-10-08T12:00:00", "instruction": "write the notes",
               "final": {}, "log": [],
               "plan": {"packages": {"mode": "ask", "requests": []},
                        "contract": {"goal": "notes.md says hello"}},
               "versions": {"count": 2, "head": "v2", "branch": "main"}}
        stub = {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens": 1000000,
                                                  "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/continue": {"run": "run_9"}}
        steps = (
            "activeTab='experience';"
            "document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.id==='experience'));"
            "experienceState.unit='task-done';await xpLoadRuns();await wait();"
            "const text=(el)=>el?el.textContent.replace(/\s+/g,' ').trim():'';"
            "const row=document.querySelector('.xp-continue-next');"
            "out.row=!!row;out.label=text(row&&row.querySelector('label span'));"
            "out.plainRow=!!document.querySelector('p.xp-continue-row');"
            "row.querySelector('[data-xp-continue=\"run_9\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "out.postsEmpty=window.__posts.filter(p=>p.p==='/api/work/continue').length;"
            "const box=row.querySelector('textarea');box.value='Now add a summary page.';"
            "box.dispatchEvent(new Event('input',{bubbles:true}));"
            "row.querySelector('[data-xp-continue=\"run_9\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "out.posts=window.__posts.filter(p=>p.p==='/api/work/continue').map(p=>p.body);")
        out = self._render(stub, steps)
        self.assertTrue(out["row"])
        self.assertEqual(out["label"], "What to do next, building on what is done")
        self.assertFalse(out["plainRow"], "no plain Continue for a finished run")
        self.assertEqual(out["postsEmpty"], 0, "nothing said, nothing sent")
        self.assertEqual(len(out["posts"]), 1)
        self.assertEqual(out["posts"][0]["instruction"], "Now add a summary page.")
        self.assertNotIn("from_version", out["posts"][0])


if __name__ == "__main__":
    unittest.main()

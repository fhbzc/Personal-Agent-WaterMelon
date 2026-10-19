"""The user's experience: stage graphs per piece of work, patterns and
principles over the nature records, and the search that joins them."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from datetime import timedelta

from support import TemporaryAgent

from personalized_agent.common.events.models import utcnow
from personalized_agent.heartbeat.experience.store import ExperienceStore
from personalized_agent.heartbeat.states.tasks import Kept, Project, Projects, Task


class _Models:
    """A model whose answer depends on what it is asked."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def invoke(self, request, role=""):
        self.asked.append(request)
        text = self.answer(request) if callable(self.answer) else self.answer

        class Reply:
            pass

        reply = Reply()
        reply.text = json.dumps(text) if not isinstance(text, str) else text
        reply.model, reply.provider, reply.usage = "test-model", "test", None
        reply.tool_calls = []
        return reply


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.experience = self.app.state_heartbeat.experience
        self.store = ExperienceStore(self.app.db)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _states(self, titles, minutes_apart=10):
        from personalized_agent.agent.states.store import State

        began = utcnow() - timedelta(hours=5)
        made = []
        for index, title in enumerate(titles):
            at = began + timedelta(minutes=minutes_apart * index)
            state = self.app.states.add(State(title=title, detail=title,
                                              starts_at=at,
                                              ends_at=at + timedelta(minutes=5)))
            made.append(state.id)
        return made

    def _task(self, task_id, members, title):
        task = Task(id=task_id, members=list(members),
                    described={"title": title, "goal": "the goal"})
        Kept(self.app.db).save([task])
        return task


class StagesAreWrittenAsTheWorkGoesTests(_Base):

    def test_new_states_become_stages_and_edges(self) -> None:
        ids = self._states(["asked for project rows", "the agent built them",
                            "tried them: rows do not expand", "asked for a fix",
                            "still typing the next request"])
        task = self._task("task-a", ids, "Group tasks into projects")

        def answer(request):
            return {"new_stages": [
                {"ref": "n1", "kind": "plan", "medium": "code",
                 "card": "root", "summary": "asked for rows",
                 "states": [ids[0]]},
                {"ref": "n2", "kind": "execution", "medium": "code",
                 "card": "root", "summary": "built",
                 "states": [ids[1]],
                 "edges": [{"kind": "continues", "to": "n1"}]},
                {"ref": "n3", "kind": "evaluation", "medium": "code",
                 "card": "root", "summary": "rejected",
                 "states": [ids[2], ids[3]],
                 "evaluation": {"verdict": "rejected",
                                "criteria": ["it must expand"],
                                "words": ["还是没显示"]},
                 # An edge to something unknown, and one to the future: dropped.
                 "edges": [{"kind": "evaluates", "to": "n2"},
                           {"kind": "depends_on", "to": "stage_nowhere"}]}],
                "still_open": [ids[4]]}

        writer = self.experience.stages_writer()
        writer.models = _Models(answer)
        unit = writer.units([task], [])[0]
        said = writer.extend(unit)
        self.assertEqual(said["stages"], 3)
        stages = self.store.stages(["task-a"])
        self.assertEqual([s["kind"] for s in stages],
                         ["plan", "execution", "evaluation"])
        # Each an attempt at the work's root: no piece of it was named.
        root = self.store.root("task-a")
        self.assertEqual({s["card_id"] for s in stages}, {root["id"]})
        self.assertEqual(stages[2]["evaluation"]["words"], ["还是没显示"])
        self.assertEqual(stages[0]["written_by"]["model"], "test-model")
        edges = self.store.edges([s["id"] for s in stages])
        self.assertEqual(sorted(e["kind"] for e in edges),
                         ["continues", "evaluates"])
        # What is still open is kept - and shown again with what comes next,
        # not on its own.
        self.assertEqual(self.store.mark("task-a")["open_tail"], [ids[4]])
        self.assertEqual(writer.pending(unit), [])

    def test_the_frontier_is_what_the_model_is_shown_not_the_whole_graph(self) -> None:
        ids = self._states(["one", "two", "three"])
        task = self._task("task-a", ids, "A task")
        writer = self.experience.stages_writer()
        writer.models = _Models({"new_stages": [
            {"ref": "n1", "kind": "execution", "card": "root",
             "summary": "first", "states": [ids[0]]}], "still_open": []})
        writer.extend(writer.units([task], [])[0])
        more = self._states(["four"])
        task.members = ids + more
        Kept(self.app.db).save([task])
        writer.models = _Models({"new_stages": [], "still_open": []})
        writer.extend(writer.units([task], [])[0])
        sent = writer.models.asked[0].messages[0]["content"]
        self.assertIn("first", sent)           # the frontier
        self.assertIn(more[0], sent)            # what is new
        self.assertNotIn(f"[{ids[0]}]", sent)   # not the old states again


class TheWorkIsATreeOfCardsTests(_Base):
    """The root is the goal, the deliverables under it, the parts under
    those; a stage is an attempt inside a card."""

    def _answer(self, ids):
        return {
            "cards": [
                # A part named before the deliverable it is a piece of.
                {"ref": "c2", "parent": "c1", "title": "Rows expand",
                 "type": "code", "statement": "a project row opens to its tasks",
                 "specification": [
                     {"requirement": "it opens on one click",
                      "source": "the user said: '一点就开'"},
                     {"requirement": "it is fast"}],          # no source: dropped
                 "checked_by": {"by": "user", "what": "clicks a row"},
                 "why": {"why": "the panel is read by project", "from": "inferred"}},
                {"ref": "c1", "parent": "root", "title": "Project rows",
                 "type": "code", "statement": "the panel groups tasks by project",
                 "checked_by": {"by": "magic"}},
                {"ref": "c3", "parent": "c1", "title": "Rows as a table",
                 "type": "code", "statement": "projects drawn as a table"}],
            "new_stages": [
                {"ref": "n1", "kind": "plan", "medium": "code", "card": "c1",
                 "summary": "split the rows into pieces", "states": [ids[0]]},
                {"ref": "n2", "kind": "execution", "medium": "code", "card": "c2",
                 "summary": "built the expanding rows", "states": [ids[1]]},
                {"ref": "n3", "kind": "evaluation", "medium": "code",
                 "card": "card_nowhere", "summary": "judged", "states": [ids[2]]}],
            "cards_changed": [{"card": "c3", "status": "set_aside",
                               "replaced_by": "c2",
                               "why": "a table hid the tasks"}],
            "still_open": []}

    def _built(self):
        ids = self._states(["planned", "built", "judged"])
        task = self._task("task-a", ids, "Group tasks into projects")
        writer = self.experience.stages_writer()
        writer.models = _Models(self._answer(ids))
        unit = writer.units([task], [])[0]
        said = writer.extend(unit)
        return writer, unit, task, ids, said

    def test_cards_are_made_under_the_card_they_serve(self) -> None:
        writer, unit, _task, _ids, said = self._built()
        self.assertEqual(said["stages"], 3)
        root, cards = writer.tree(unit)
        self.assertEqual(root["title"], "Group tasks into projects")
        by_title = {c["title"]: c for c in cards.values()}
        self.assertEqual(by_title["Project rows"]["parent_id"], root["id"])
        self.assertEqual(by_title["Rows expand"]["parent_id"],
                         by_title["Project rows"]["id"])
        # Only what the user set, with where they set it.
        self.assertEqual([one["requirement"] for one in
                          by_title["Rows expand"]["specification"]],
                         ["it opens on one click"])
        self.assertEqual(by_title["Rows expand"]["checked_by"]["by"], "user")
        self.assertEqual(by_title["Project rows"]["checked_by"]["by"], "")

    def test_each_stage_is_an_attempt_inside_its_card(self) -> None:
        writer, unit, _task, _ids, _said = self._built()
        root, cards = writer.tree(unit)
        by_title = {c["title"]: c for c in cards.values()}
        card_of = {s["summary"]: s["card_id"] for s in self.store.stages(["task-a"])}
        self.assertEqual(card_of["split the rows into pieces"],
                         by_title["Project rows"]["id"])
        self.assertEqual(card_of["built the expanding rows"],
                         by_title["Rows expand"]["id"])
        # A card that does not exist: the attempt is the root's.
        self.assertEqual(card_of["judged"], root["id"])
        # A card with attempts is under way without being told.
        self.assertEqual(by_title["Rows expand"]["status"], "working")
        self.assertEqual(by_title["Rows expand"]["task_id"], "task-a")

    def test_a_dropped_route_is_set_aside_with_what_replaced_it(self) -> None:
        writer, unit, _task, _ids, _said = self._built()
        _root, cards = writer.tree(unit)
        by_title = {c["title"]: c for c in cards.values()}
        dropped = by_title["Rows as a table"]
        self.assertEqual(dropped["status"], "set_aside")
        self.assertEqual(dropped["replaced_by"], by_title["Rows expand"]["id"])
        self.assertIn("hid the tasks", dropped["status_why"])

    def test_the_writer_is_shown_the_tree(self) -> None:
        writer, _unit, task, ids, _said = self._built()
        more = self._states(["judged again"])
        task.members = ids + more
        Kept(self.app.db).save([task])
        writer.models = _Models({"new_stages": [], "still_open": []})
        writer.extend(writer.units([task], [])[0])
        sent = writer.models.asked[0].messages[0]["content"]
        self.assertIn("THE CARDS:", sent)
        self.assertIn("ROOT", sent)
        self.assertIn("DELIVERABLE · code · working: Project rows", sent)
        self.assertIn("PART · code · working: Rows expand", sent)
        # The attempts of a card still going; a set-aside card is one line.
        self.assertIn("built the expanding rows", sent)
        self.assertIn("set_aside (a table hid the tasks): Rows as a table", sent)

    def test_the_levels_are_where_a_card_sits(self) -> None:
        writer, unit, _task, _ids, _said = self._built()
        graph = {"cards": self.experience.cards_of(writer, unit)}
        levels = {c["title"]: c["level"] for c in graph["cards"]}
        self.assertEqual(levels["Group tasks into projects"], "root")
        self.assertEqual(levels["Project rows"], "deliverable")
        self.assertEqual(levels["Rows expand"], "part")
        # Read in the order of the tree: each card after the one above it.
        order = [c["title"] for c in graph["cards"]]
        self.assertLess(order.index("Project rows"), order.index("Rows expand"))

    def test_a_task_that_joins_a_project_becomes_one_of_its_deliverables(self) -> None:
        writer, unit, task, _ids, _said = self._built()
        task_root = self.store.root("task-a")
        project = Project(id="project-1", name="The release",
                          why="ship it", tasks=["task-a"])
        Projects(self.app.db).save(project)
        project_unit = writer.units([task], [project])[0]
        root, cards = writer.tree(project_unit)
        self.assertEqual(root["title"], "The release")
        self.assertEqual(cards[task_root["id"]]["parent_id"], root["id"])
        # And when it leaves, its root stands on its own again.
        alone = writer.units([task], [])[0]
        own_root, _cards = writer.tree(alone)
        self.assertEqual(own_root["id"], task_root["id"])
        self.assertEqual(own_root["parent_id"], "")

    def test_a_task_merged_into_another_brings_its_cards(self) -> None:
        writer, _unit, task, ids, _said = self._built()
        survivor = self._task("task-b", ids, "The survivor")
        task.retired_at = utcnow().isoformat()
        task.born_of = {"joined_into": "task-b"}
        Kept(self.app.db).save([task])
        writer.follow_the_tasks([survivor], [task], [])
        root, cards = writer.tree(writer.units([survivor], [])[0])
        titles = {c["title"] for c in cards.values()}
        self.assertIn("Rows expand", titles)
        # The merged task's goal is a deliverable of the one it went into.
        merged = next(c for c in cards.values()
                      if c["title"] == "Group tasks into projects")
        self.assertEqual(merged["parent_id"], root["id"])

    def test_an_agent_doing_the_work_sees_what_each_attempt_was_at(self) -> None:
        import contextlib

        from personalized_agent.agent.work.tools import compact_experience
        from personalized_agent.heartbeat.experience.retrieve import Retriever

        class Flat:
            """Every text alike: what is found is decided by the graph."""

            def available(self):
                return True

            def loaded(self):
                return contextlib.nullcontext(self)

            def embed(self, texts):
                return [[1.0, 0.0] for _ in texts]

            def for_states(self, texts):
                return [(one.id, [1.0, 0.0]) for one in texts]

        self._built()
        said = Retriever(self.app.db, self.app.library, Flat()).retrieve(
            "split the rows into pieces, then built the expanding rows", seeds=5)
        # A line each: the card it was an attempt at, and its level ...
        lines = {line["summary"]: line for line in compact_experience(said)["stages"]}
        self.assertEqual(lines["built the expanding rows"]["card"], "Rows expand")
        self.assertEqual(lines["built the expanding rows"]["level"], "part")
        self.assertEqual(lines["split the rows into pieces"]["level"], "deliverable")
        # ... and opened, what done meant for it and who checks it.
        from personalized_agent.agent.work.tools import stage_key_info
        opened = {s["summary"]: stage_key_info(s, s["card"]) for s in said["stages"]}
        at = opened["built the expanding rows"]["attempt_at"]
        self.assertEqual((at["title"], at["level"]), ("Rows expand", "part"))
        self.assertEqual(at["checked_by"]["by"], "user")


class WhatAStageShowsIsWrittenWithItTests(_Base):
    """The stage writer says what each stage shows about the user in the same
    answer - so a stage carries its natures from the moment it is written."""

    def _write(self, natures):
        ids = self._states(["sent the figure back", "redrew it"])
        task = self._task("task-a", ids, "The paper's figures")
        writer = self.experience.stages_writer()
        writer.models = _Models({"new_stages": [
            {"ref": "n1", "kind": "evaluation", "medium": "figure", "card": "root",
             "summary": "the user sent the figure back", "states": [ids[0]],
             "evaluation": {"verdict": "rejected", "words": ["重点不清楚"]},
             "references": [{"id": "r1", "location": "D:/papers/trade/fig1.pdf",
                             "what": "the first draft of the figure"}],
             "natures": natures},
            {"ref": "n2", "kind": "execution", "medium": "figure", "card": "root",
             "summary": "redrew it", "states": [ids[1]], "natures": []}],
            "still_open": []})
        said = writer.extend(writer.units([task], [])[0])
        return said, {s["summary"]: s for s in self.store.stages(["task-a"])}

    def test_its_natures_are_written_and_linked_to_it(self) -> None:
        said, stages = self._write([
            {"aspect": "evaluation",
             "title": "The user sends a figure back when its point does not read at a glance.",
             "content": "c", "confidence": 6, "evidence": ["'重点不清楚'"],
             "example": {"what": "the first draft of the trade-flow figure, sent "
                                 "back because its point did not read",
                         "references": ["r1"]}},
            {"aspect": "output",
             "title": "The user wants one finding per figure, stated in its title.",
             "content": "c",
             "example": {"said": "重点不清楚", "what": "the redrawn figure titled with its finding"}},
            # An example that is only the model's account - no words of the
            # user's, no place it can be opened: not a case, and not kept.
            {"aspect": "output", "title": "The user likes tall figures.",
             "content": "c", "example": {"what": "the redrawn figure"}},
            # Not about how they evaluate or what they want made: not kept.
            {"aspect": "working", "title": "The user works late.", "content": "c",
             "example": {"what": "x"}},
            # No example: not kept.
            {"aspect": "output", "title": "The user likes short figures.",
             "content": "c"}])
        self.assertEqual(said["stages"], 2)
        judged = stages["the user sent the figure back"]
        linked = self.store.natures_of_stages([judged["id"]])[judged["id"]]
        self.assertEqual(len(linked), 2)
        from personalized_agent.heartbeat.experience.patterns import nature_records
        kept = {r.metadata.get("aspect"): r for r in nature_records(self.app.library)
                if r.id in linked}
        self.assertEqual(sorted(kept), ["evaluation", "output"])
        # Each with the case it was seen in - and where that can be opened.
        from personalized_agent.heartbeat.experience.stage_natures import example_of
        figure = kept["evaluation"]
        self.assertIn("trade-flow figure", example_of(figure)["what"])
        self.assertEqual(example_of(figure)["references"][0]["location"],
                         "D:/papers/trade/fig1.pdf")
        self.assertIn("For example: the first draft", figure.body)
        self.assertEqual(figure.metadata["nature_kind"], "work")

    def test_a_stage_is_not_read_again_for_them(self) -> None:
        _said, stages = self._write([])
        # Both were answered for - one with nothing to say - so neither waits
        # for the pass that reads stages written without them.
        waiting = {s["id"] for s in self.store.stages_without_natures(50)}
        self.assertFalse(waiting & {s["id"] for s in stages.values()})

    def test_the_writer_is_told_what_work_nature_is(self) -> None:
        from personalized_agent.heartbeat.experience.stages import STAGE_PROMPT
        self.assertIn("HOW THE USER EVALUATES A RESULT", STAGE_PROMPT)
        self.assertIn("WHAT OUTPUT THE USER WANTS", STAGE_PROMPT)
        self.assertIn('"aspect": "evaluation|output"', STAGE_PROMPT)
        self.assertIn("EACH ONE WITH ITS EXAMPLE", STAGE_PROMPT)


class TheTreeShownStaysSmallTests(_Base):
    """Every card one line; the attempts only of the cards being worked on
    now; a finished branch one line; anything else one open_card away."""

    def _project(self):
        a_ids, b_ids = self._states(["a1"]), self._states(["b1"])
        a = self._task("task-a", a_ids, "A")
        b = self._task("task-b", b_ids, "B")
        project = Project(id="project-1", name="P", why="ship it",
                          tasks=["task-a", "task-b"])
        Projects(self.app.db).save(project)
        writer = self.experience.stages_writer()
        unit = writer.units([a, b], [project])[0]
        root, _cards = writer.tree(unit)
        made = {}
        for n in range(12):
            card = self.store.add_card({"task_id": "task-a" if n % 2 else "task-b",
                                        "parent_id": root["id"], "title": f"piece {n}",
                                        "status": "working"})
            made[n] = card
            self.store.add_stage({"task_id": card["task_id"], "card_id": card["id"],
                                  "kind": "execution", "summary": f"attempt at piece {n}",
                                  "states": [], "began": f"2026-09-{10 + n:02d}",
                                  "ended": f"2026-09-{10 + n:02d}"})
        done = self.store.add_card({"task_id": "task-a", "parent_id": root["id"],
                                    "title": "finished section", "status": "done"})
        for part in ("first part", "second part"):
            self.store.add_card({"task_id": "task-a", "parent_id": done["id"],
                                 "title": part, "status": "done"})
        return writer, unit, made, done

    def test_every_card_is_a_line_and_only_the_active_show_attempts(self) -> None:
        writer, unit, made, _done = self._project()
        text, _stages, _root, _cards = writer._frontier(unit, ["task-b"])
        for n in range(12):
            self.assertRegex(text, rf"piece {n}\b")
        shown = [n for n in range(12) if re.search(rf"attempt at piece {n}\b", text)]
        self.assertEqual(len(shown), 8)
        # The new states are task-b's: its six cards first, then the two most
        # recently worked on of the rest.
        self.assertEqual(sorted(shown), [0, 2, 4, 6, 8, 9, 10, 11])
        self.assertIn("(1 attempts - open_card to read them)", text)

    def test_a_finished_branch_is_one_line(self) -> None:
        writer, unit, _made, done = self._project()
        text, _stages, _root, _cards = writer._frontier(unit)
        self.assertIn("finished section", text)
        self.assertIn("(2 cards under it, all done or set aside", text)
        self.assertNotIn("first part", text)

    def test_a_card_opened_is_whole_and_only_this_works(self) -> None:
        writer, unit, made, done = self._project()
        said = writer.open_cards(unit, [made[3]["id"], done["id"], "card_elsewhere"])
        first, branch, outside = said["cards"]
        self.assertEqual([a["summary"] for a in first["attempts"]],
                         ["attempt at piece 3"])
        self.assertEqual(sorted(p["title"] for p in branch["pieces"]),
                         ["first part", "second part"])
        self.assertIn("error", outside)

    def test_the_writer_opens_a_card_before_it_answers(self) -> None:
        writer, unit, made, _done = self._project()
        more = self._states(["another try at piece 1"])
        task_a = next(t for t in unit["tasks"] if t.id == "task-a")
        task_a.members = list(task_a.members) + more
        Kept(self.app.db).save([task_a])
        unit = writer.units(unit["tasks"], [Projects(self.app.db).all()[0]])[0]
        asked = []

        class Opens:
            def invoke(self, request, role=""):
                asked.append(request)
                reply = type("R", (), {})()
                reply.model, reply.provider, reply.usage = "m", "p", None
                reply.stop_reason, reply.raw_content = "end_turn", ""
                if len(asked) == 1:
                    call = type("C", (), {"id": "c1", "name": "open_card",
                                          "arguments": {"ids": [made[1]["id"]]}})()
                    reply.text, reply.tool_calls = "", [call]
                    return reply
                reply.tool_calls = []
                reply.text = json.dumps({"new_stages": [{
                    "ref": "n1", "kind": "execution", "card": made[1]["id"],
                    "summary": "tried piece 1 again", "states": more}],
                    "still_open": []})
                return reply

        writer.models = Opens()
        said = writer.extend(unit)
        self.assertEqual(said["stages"], 1)
        self.assertEqual([t.name for t in asked[0].tools], ["open_card"])
        opened = json.loads(asked[1].messages[-1]["content"][0]["content"])
        self.assertEqual(opened["cards"][0]["attempts"][0]["summary"],
                         "attempt at piece 1")
        card_of = {s["summary"]: s["card_id"] for s in self.store.stages(["task-a"])}
        self.assertEqual(card_of["tried piece 1 again"], made[1]["id"])


class ACutOffAnswerIsNeverAppliedTests(_Base):
    """An answer stopped at its length limit is asked again with fewer
    states; nothing in it is applied, and nothing marked as read."""

    def _many(self):
        from personalized_agent.heartbeat.experience.stages import BATCH_SIZE
        BATCH_SIZE.clear()
        ids = self._states([f"step {n}" for n in range(40)], minutes_apart=1)
        task = self._task("task-a", ids, "Long work")
        writer = self.experience.stages_writer()
        return writer, writer.units([task], [])[0], ids

    @staticmethod
    def _model(most):
        asked = []

        class Model:
            def invoke(self, request, role=""):
                asked.append(request)
                content = request.messages[0]["content"]
                shown = re.findall(r"^- \[([^\]]+)\]", content.split("THE NEW STATES", 1)[1],
                                   re.M)
                reply = type("R", (), {})()
                reply.model, reply.provider, reply.usage = "m", "p", None
                reply.raw_content, reply.tool_calls = "", []
                if len(shown) > most:
                    reply.stop_reason = "max_tokens"
                    reply.text = '{"new_stages": [{"ref": "n1", "kind": "execu'
                    return reply
                reply.stop_reason = "end_turn"
                reply.text = json.dumps({"new_stages": [{
                    "ref": "n1", "kind": "execution", "card": "root",
                    "summary": f"{len(shown)} steps", "states": shown}],
                    "still_open": []})
                return reply

        return Model(), asked

    def test_it_is_asked_again_with_half_as_many(self) -> None:
        writer, unit, ids = self._many()
        writer.models, asked = self._model(most=20)
        said = writer.extend(unit, budget=4)
        self.assertNotIn("error", said)
        self.assertEqual(said["cut_off"], 1)
        self.assertEqual([s["summary"] for s in self.store.stages(["task-a"])],
                         ["20 steps", "20 steps"])
        self.assertEqual(sorted(self.store.mark("task-a")["states_seen"]), sorted(ids))

    def test_the_next_round_starts_at_the_size_that_fitted(self) -> None:
        writer, unit, ids = self._many()
        writer.models, asked = self._model(most=20)
        writer.extend(unit, budget=1)          # one call: cut off, nothing read
        self.assertEqual(self.store.stages(["task-a"]), [])
        asked.clear()
        writer.extend(unit, budget=4)
        self.assertEqual(len(asked), 2, "started at forty again")

    def test_cut_off_even_at_the_fewest_is_an_error_and_nothing_is_read(self) -> None:
        writer, unit, ids = self._many()
        writer.models, asked = self._model(most=0)
        said = writer.extend(unit, budget=4)
        self.assertIn("cut off even for 10 states", said["error"])
        self.assertEqual(self.store.stages(["task-a"]), [])
        self.assertEqual(self.store.mark("task-a")["states_seen"], [])
        self.assertEqual(writer.pending(unit), ids)


class StagesAreWrittenInTheDisplayLanguageTests(_Base):
    """What learning writes is the agent's own - stages, cards and natures in
    English, whatever the user reads in (2026-10-18); the agents' own work
    for the user is in the display language."""

    def test_the_stage_writer_writes_in_english_whatever_is_displayed(self) -> None:
        self.app.config.set("display_language", "zh")
        ids = self._states(["one"])
        task = self._task("task-a", ids, "A")
        writer = self.experience.stages_writer()
        writer.models = _Models({"new_stages": [], "still_open": [ids[0]]})
        writer.extend(writer.units([task], [])[0])
        asked = writer.models.asked[0]
        self.assertNotIn("{language}", asked.system)
        # Said once, at the top of what is sent: in English, the user's own
        # words where they are quoted as they wrote them.
        sent = asked.language.compose(asked.system, self.app.config)
        first = sent.splitlines()[0]
        self.assertNotIn("Chinese", first)
        self.assertIn("English", first)

    def test_and_so_are_the_agents_working_on_a_project(self) -> None:
        from personalized_agent.agent.work.runner import (AGENT_SYSTEM, REVIEWER_SYSTEM,
                                                          WORK_LANGUAGE)
        # Not inside the prompts: given with each call, and put at the top.
        self.assertNotIn("{language}", AGENT_SYSTEM)
        self.assertNotIn("{language}", REVIEWER_SYSTEM)
        # The user's decision (2026-10-18): what is delivered in the display
        # language unless the instruction names another; the run's own notes
        # - its cards and stages - in English.
        rules = {one["rule"]: one["what"] for one in WORK_LANGUAGE.rules()}
        self.assertIn("DELIVER", rules["display"])
        self.assertIn("unless the user's instruction asks for another language", rules["display"])
        self.assertIn("the cards, stages", rules["internal"])
        self.app.config.set("display_language", "zh")
        said = WORK_LANGUAGE.instruction(self.app.config)
        self.assertTrue(said.startswith("WRITE IT IN THIS LANGUAGE: Chinese"))
        self.assertIn("EXCEPTION, for your own notes on the work", said)
        self.app.config.set("display_language", "en")
        self.assertEqual(self.experience.language(), "English")


class StagesFollowTheirTasksTests(_Base):

    def test_a_task_joined_into_another_brings_its_stages(self) -> None:
        ids = self._states(["a", "b"])
        old = self._task("task-old", ids, "Old")
        self.store.add_stage({"task_id": "task-old", "kind": "execution",
                              "states": ids})
        survivor = self._task("task-new", ids, "New")
        old.retired_at = utcnow().isoformat()
        old.born_of = {"joined_into": "task-new"}
        Kept(self.app.db).save([old])
        self.experience.stages_writer().follow_the_tasks([survivor], [old], [])
        self.assertEqual(len(self.store.stages(["task-new"])), 1)

    def test_a_project_carried_on_brings_the_states_it_had_read(self) -> None:
        ids = self._states(["a", "b"])
        old = self._task("task-old", ids, "Old")
        self.store.set_mark("task-old", ids[:1], [])
        heir = self._task("task-new", ids, "New")
        old.retired_at = utcnow().isoformat()
        old.born_of = {"joined_into": "task-new"}
        Kept(self.app.db).save([old])
        self.experience.stages_writer().follow_the_tasks([heir], [old], [])
        self.assertEqual(self.store.mark("task-new")["states_seen"], ids[:1])
        self.assertEqual(self.store.mark("task-old")["states_seen"], [])

    def test_stages_follow_a_chain_of_carrying_on_to_its_end(self) -> None:
        ids = self._states(["a", "b"])
        first = self._task("task-a", ids, "A")
        self.store.add_stage({"task_id": "task-a", "kind": "execution", "states": ids})
        middle = self._task("task-n", ids, "N")
        last = self._task("task-o", ids, "O")
        for one, into in ((first, "task-n"), (middle, "task-o")):
            one.retired_at = utcnow().isoformat()
            one.born_of = {"joined_into": into}
        Kept(self.app.db).save([first, middle])
        self.experience.stages_writer().follow_the_tasks([last], [first, middle], [])
        self.assertEqual(len(self.store.stages(["task-o"])), 1)

    def test_a_project_carried_on_by_none_keeps_its_stages(self) -> None:
        ids = self._states(["a", "b"])
        old = self._task("task-old", ids, "Old")
        self.store.add_stage({"task_id": "task-old", "kind": "execution",
                              "states": ids})
        other = self._task("task-other", ids[:1], "Other")
        old.retired_at = utcnow().isoformat()
        old.born_of = {"joined_into": ""}
        Kept(self.app.db).save([old])
        self.experience.stages_writer().follow_the_tasks([other], [old], [])
        self.assertEqual(len(self.store.stages(["task-old"])), 1)
        self.assertEqual(self.store.stages(["task-other"]), [])

    def test_joining_a_project_adds_edges_and_leaving_takes_only_those(self) -> None:
        a_ids, b_ids = self._states(["a1", "a2"]), self._states(["b1"])
        self._task("task-a", a_ids, "A")
        self._task("task-b", b_ids, "B")
        first = self.store.add_stage({"task_id": "task-a", "kind": "execution",
                                      "states": a_ids, "began": "2026-10-01",
                                      "ended": "2026-10-01"})
        later = self.store.add_stage({"task_id": "task-b", "kind": "execution",
                                      "states": b_ids, "began": "2026-10-02",
                                      "ended": "2026-10-02"})
        project = Project(id="project-1", name="P", tasks=["task-a", "task-b"])
        Projects(self.app.db).save(project)
        writer = self.experience.stages_writer()
        writer.models = _Models({"edges": [
            {"from": later["id"], "to": first["id"], "kind": "depends_on"},
            # The other way round would point at the future: refused.
            {"from": first["id"], "to": later["id"], "kind": "depends_on"}]})
        writer.joins([project])
        self.assertEqual(len(self.store.edges()), 1)
        project.tasks = ["task-a"]
        writer.joins([project])
        self.assertEqual(self.store.edges(), [])


class LearningAProjectThatEndedTests(_Base):

    def test_one_ended_while_it_waited_ends_quietly(self) -> None:
        with self.assertNoLogs("personalized_agent.heartbeat.experience.service", "ERROR"):
            self.experience._learn_one("p-gone")
        said = self.experience.learning_progress["p-gone"]
        self.assertTrue(said["gone"])
        self.assertFalse(said["running"])
        self.assertNotIn("error", said)
        self.assertEqual(self.store.learning("p-gone")["until"], "")


class PatternsAndPrinciplesTests(_Base):

    def _records(self):
        from personalized_agent.data_manage.library import USER_NATURE, Record

        made = []
        for theme, lines in (
                ("tries", ["The user tries the dashboard on live data before "
                           "accepting a fix.",
                           "The user tries every UI fix by hand on live data.",
                           "The user accepts a fix only after trying it on "
                           "live data.",
                           "The user checks a fix on the live page before "
                           "accepting it."]),
                ("plans", ["The user asks for a proposal before any code "
                           "changes.",
                           "The user wants a plan first and no code changes.",
                           "The user asks for a proposal and picks options "
                           "before code changes.",
                           "Before code changes the user asks for a "
                           "proposal."])):
            for line in lines:
                made.append(self.app.library.write(Record(
                    folder=USER_NATURE, title=line, summary=line,
                    source="heartbeat",
            metadata={"nature_kind": "work"})).id)
        return made

    def _model(self):
        def answer(request):
            if "PRINCIPLES BEHIND THEM" in request.system:
                ids = re.findall(r"\[(pattern[^\]]*)\]",
                                 request.messages[0]["content"])
                return {"principles": [{"statement": "The user judges by "
                                        "the real thing.", "detail": "d",
                                        "patterns": ids}]}
            return {"statement": "A way of working.", "detail": "d"}
        return _Models(answer)

    def test_records_become_patterns_and_patterns_principles(self) -> None:
        self._records()
        builder = self.experience.patterns_builder()
        builder.models = self._model()
        said = builder.build()
        self.assertGreaterEqual(said["patterns"], 1)
        principles = self.store.patterns("principle")
        self.assertEqual(len(principles), 1)
        patterns = self.store.patterns("pattern")
        self.assertTrue(all(p["parent"] == principles[0]["id"] for p in patterns))
        self.assertEqual(principles[0]["written_by"]["model"], "test-model")
        # Built again with nothing new, the same groups keep their ids and
        # words, and nothing is asked.
        before = {p["id"] for p in patterns}
        builder.models = self._model()
        builder.build()
        self.assertEqual({p["id"] for p in self.store.patterns("pattern")}, before)
        self.assertEqual(builder.models.asked, [])


    def test_a_build_stopped_part_way_carries_on_where_it_stopped(self) -> None:
        self._records()
        builder = self.experience.patterns_builder()
        builder.models = self._model()
        first = builder.build(budget=1)
        self.assertEqual(first["calls"], 1)
        self.assertTrue(first.get("unfinished"))
        named = {p["id"]: p["statement"] for p in self.store.patterns("pattern")
                 if p["statement"]}
        self.assertEqual(len(named), 1, "the one name written was not kept")
        builder.models = self._model()
        second = builder.build()
        self.assertFalse(second.get("unfinished"), second)
        # The name already written was not asked for again.
        asked = len(builder.models.asked)
        self.assertEqual(asked, len(self.store.patterns("pattern")) - 1 + 1)
        for key, statement in named.items():
            self.assertEqual(next(p for p in self.store.patterns("pattern")
                                  if p["id"] == key)["statement"], statement)
        self.assertEqual(len(self.store.patterns("principle")), 1)
        builder.models = self._model()
        builder.build()
        self.assertEqual(builder.models.asked, [], "a finished build asked again")

    def test_new_records_are_appended_not_regrouped(self) -> None:
        from personalized_agent.data_manage.library import USER_NATURE, Record

        self._records()

        def answer(request):
            content = request.messages[0]["content"]
            if "PRINCIPLES BEHIND THEM" in request.system:
                ids = re.findall(r"\[(pattern[^\]]*)\]", content)
                return {"principles": [{"statement": "Judges by the real thing.",
                                        "detail": "d", "patterns": ids}]}
            if "PUT THESE NEW WAYS" in request.system:
                principle = re.findall(r"\[(principle[^\]]*)\]", content)[0]
                new = re.findall(r"\[(pattern[^\]]*)\]", content)
                return {"placed": {one: principle for one in new},
                        "new_principles": []}
            return {"statement": "A way of working.", "detail": "d"}

        builder = self.experience.patterns_builder()
        builder.models = _Models(answer)
        builder.build()
        before = {p["id"]: (p["statement"], len(p["members"]))
                  for p in self.store.patterns("pattern")}
        principles = self.store.patterns("principle")
        # One more like an existing pattern: appended, words kept, nothing asked.
        self.app.library.write(Record(
            folder=USER_NATURE, source="heartbeat",
            metadata={"nature_kind": "work"},
            title="The user tries the dashboard fix on live data first.",
            summary="The user tries the dashboard fix on live data first."))
        builder.models = _Models(answer)
        said = builder.build()
        self.assertEqual(said["appended"], 1, said)
        self.assertEqual(said["new_patterns"], 0, said)
        self.assertEqual(builder.models.asked, [])
        after = {p["id"]: (p["statement"], len(p["members"]))
                 for p in self.store.patterns("pattern")}
        self.assertEqual(set(after), set(before), "the patterns were regrouped")
        self.assertEqual(sum(n for _s, n in after.values()),
                         sum(n for _s, n in before.values()) + 1)
        # Something new, three times over: a new pattern, named and placed
        # under the principle there is.
        for line in ("The user cooks dinner every evening at seven.",
                     "Every evening at seven the user cooks dinner.",
                     "The user cooks dinner at seven each evening."):
            self.app.library.write(Record(folder=USER_NATURE, source="heartbeat",
            metadata={"nature_kind": "work"},
                                          title=line, summary=line))
        builder.models = _Models(answer)
        said = builder.build()
        self.assertEqual(said["new_patterns"], 1, said)
        kinds = [r.system[:30] for r in builder.models.asked]
        # Named and placed. The principle, now three patterns where it was
        # worded at two, changed by one pattern - fewer than the two a
        # principle must change by to be worded again: its words stand.
        self.assertEqual(len(builder.models.asked), 2, kinds)
        self.assertNotIn("SAY THE PRINCIPLE", " ".join(kinds))
        self.assertEqual([p["id"] for p in self.store.patterns("principle")],
                         [p["id"] for p in principles])
        self.assertTrue(all(p["parent"] for p in self.store.patterns("pattern")))

class FindingExperienceTests(_Base):

    def test_stages_lead_through_states_and_records_to_principles(self) -> None:
        from personalized_agent.data_manage.library import USER_NATURE, Record

        ids = self._states(["dashboard rows", "fix", "check"])
        plan = self.store.add_stage({"task_id": "t", "kind": "plan",
                                     "summary": "plan the dashboard project rows",
                                     "states": [ids[0]], "began": "1", "ended": "1"})
        done = self.store.add_stage({"task_id": "t", "kind": "evaluation",
                                     "summary": "tried the dashboard rows on live "
                                                "data and rejected them",
                                     "states": [ids[2]], "began": "3", "ended": "3"})
        far = self.store.add_stage({"task_id": "t", "kind": "delivery",
                                    "summary": "shipped", "states": [ids[1]],
                                    "began": "4", "ended": "4"})
        self.store.add_edge(done["id"], plan["id"], "evaluates")
        self.store.add_edge(far["id"], done["id"], "continues")
        record = self.app.library.write(Record(
            folder=USER_NATURE, title="The user tries fixes on live data.",
            summary="tries", source="heartbeat",
            metadata={"nature_kind": "work"}))
        self.app.db.execute("INSERT INTO nature_links (state_id, record_id, "
                            "run_id, created_at) VALUES (?, ?, 'r', 'now')",
                            (ids[2], record.id))
        pattern = self.store.save_pattern({"layer": "pattern", "statement":
                                           "Tries it by hand", "members": [record.id],
                                           "support": 1, "nature_kind": "work"})
        self.store.save_pattern({"layer": "principle", "statement":
                                 "Judges on the real thing",
                                 "members": [pattern["id"]], "support": 1,
                                 "nature_kind": "work"})
        said = self.experience.retriever().retrieve(
            "evaluate the dashboard rows", steps=1, seeds=1,
            kept={"principle": 5, "pattern": 5, "nature": 5})
        found = {s["id"]: s["found"] for s in said["stages"]}
        self.assertEqual(found.get(done["id"]), "matched")
        self.assertIn(plan["id"], found)       # one step upstream
        self.assertIn(far["id"], found)        # one step downstream
        # In order: what came before, the match, what came after.
        chain = said["chains"][0]["stages"]
        self.assertEqual([s["id"] for s in chain],
                         [plan["id"], done["id"], far["id"]])
        self.assertEqual([s["position"] for s in chain], [-1, 0, 1])
        kinds = [rule["kind"] for rule in said["rules"]]
        self.assertEqual(kinds[0], "principle")
        self.assertIn("pattern", kinds)

    def test_past_runs_are_linked_by_run(self) -> None:
        from personalized_agent.data_manage.library import USER_NATURE, Record
        from personalized_agent.heartbeat.experience.retrieve import backfill_links

        ids = self._states(["Rebuilding the package"])
        self.app.recorder.heartbeat_action("heartbeat_run1", "wrote a state",
                                           "Rebuilding the package")
        record = self.app.library.write(Record(
            folder=USER_NATURE, title="Checks figures.", summary="x",
            references=["heartbeat run: heartbeat_run1"], source="heartbeat",
            metadata={"nature_kind": "work"}))
        self.assertEqual(backfill_links(self.app.db, self.app.library), 1)
        self.assertEqual(self.store.natures_of(ids), {ids[0]: [record.id]})


class TheIndexIsBuiltByCodeTests(_Base):
    """What a stage is found by is worked out when it is written - and for
    a stage written before there was an index, the first time it is looked
    for - by code, never a model."""

    def test_a_stage_is_indexed_as_it_is_written(self) -> None:
        from personalized_agent.heartbeat.experience.index import StageIndex
        stage = self.store.add_stage({
            "task_id": "t", "kind": "evaluation", "medium": "document",
            "summary": "the robustness table was checked",
            "states": [], "began": "1", "ended": "1",
            "work": {"references": [{"id": "r1", "location": "D:/papers/trade/main.tex",
                                     "what": "the identification section"}]}})
        terms = StageIndex(self.app.db, "live").terms([stage["id"]])[stage["id"]]
        for term in ("robustness", "identification", "env:ext:.tex",
                     "env:dir:trade", "env:file:main.tex"):
            self.assertIn(term, terms)

    def test_what_a_stage_is_found_by_reads_as_words(self) -> None:
        from personalized_agent.heartbeat.experience.index import StageIndex, found_by
        stage = self.store.add_stage({
            "task_id": "t", "kind": "evaluation", "medium": "document",
            "summary": "the robustness table was rerun at 2026-10-06T18:27:02 [r6] "
                       "after commit c4e7a19, 还是没显示",
            "states": [], "began": "1", "ended": "1",
            "work": {"references": [
                {"id": "r1", "location": "D:/papers/trade/main.tex", "what": ""},
                {"id": "r2", "location": "conversation session 7d1e5b20, "
                                         "2026-10-06T18:27:02 (user)", "what": ""}]}})
        said = found_by(StageIndex(self.app.db, "live"), [stage["id"]])[stage["id"]]
        self.assertIn("robustness", said["words"])
        # Not times, reference ids, hashes, or pairs of characters cut out
        # of a sentence - those are found by, not read.
        for noise in ("2026-10-06t18", "r6", "c4e7a19", "还是"):
            self.assertNotIn(noise, said["words"])
        self.assertEqual(said["places"], [".tex", "papers", "trade"])

    def test_a_stage_from_before_the_index_is_indexed_when_looked_for(self) -> None:
        stage = self.store.add_stage({"task_id": "t", "kind": "plan",
                                      "summary": "planned the referee reply",
                                      "states": [], "began": "1", "ended": "1"})
        self.app.db.execute("DELETE FROM stage_terms")
        said = self.experience.retriever().retrieve("referee reply", kinds=["plan"])
        self.assertEqual(said["matches"][0]["id"], stage["id"])


class TheRetrievalRanksByWhatAStageIsTests(_Base):
    """Asked in words, a stage is found by what it says, and its kind,
    type, place in its card, environment, project and what it shows of the
    user count on top of that; asked in none, by those alone, each only
    breaking the ties the ones before it left."""

    def _stage(self, kind, summary, medium="document", card=None, began="2026-09-01",
               location="", verdict=""):
        return self.store.add_stage({
            "task_id": "t", "kind": kind, "medium": medium, "summary": summary,
            "card_id": card or "", "states": [], "began": began, "ended": began,
            "evaluation": {"verdict": verdict} if verdict else {},
            "work": {"references": [{"id": "r1", "location": location, "what": ""}]}
            if location else {}})

    def _ids(self, profile):
        said = self.experience.retriever().find(profile, seeds=10)
        return [one["id"] for one in said["matches"]], said

    def test_only_its_kind_and_the_next_in_the_loop_are_looked_at(self) -> None:
        own = self._stage("evaluation", "judged the figure")
        near = self._stage("adjustment", "changed the figure after it was judged")
        other = self._stage("goal", "judged the figure goal")
        ids, _said = self._ids({"kind": "evaluation", "text": "judged the figure"})
        self.assertEqual(ids[:2], [own["id"], near["id"]])
        self.assertNotIn(other["id"], ids)

    def test_a_stage_of_a_project_like_this_one_comes_first(self) -> None:
        """Two stages say the same; the one whose project's stages say what
        this work's goal says - a bakery's website beside a bakery's website
        - comes before the one from a project about something else."""
        def stage(task, summary):
            return self.store.add_stage({
                "task_id": task, "kind": "execution", "medium": "code",
                "summary": summary, "card_id": "", "states": [],
                "began": "2026-09-01", "ended": "2026-09-01"})
        for summary in ("bakery orders page", "bakery menu with prices",
                        "bakery delivery slots"):
            stage("shop", summary)
        for summary in ("telescope mount calibration", "telescope star charts",
                        "telescope eyepiece list"):
            stage("stars", summary)
        alike = stage("shop", "sign-in form with password reset")
        other = stage("stars", "sign-in form with password reset")
        ids, said = self._ids({"kind": "execution", "text": "sign-in form password",
                               "goal_text": "a bakery website with orders and delivery"})
        self.assertEqual(ids[:2], [alike["id"], other["id"]])
        why = {one["id"]: one["why"] for one in said["matches"]}
        self.assertTrue(any(one.startswith("a project like this one")
                            for one in why[alike["id"]]))
        # Without the goal, neither goes first for it.
        ids, said = self._ids({"kind": "execution", "text": "sign-in form password"})
        self.assertFalse(any(one.startswith("a project like this one")
                             for match in said["matches"] for one in match["why"]))

    def test_the_words_count_before_the_type(self) -> None:
        words = self._stage("evaluation", "the robustness table robustness checks",
                            medium="code")
        self._stage("evaluation", "looked it over", medium="document")
        ids, said = self._ids({"kind": "evaluation", "type": "document",
                               "text": "robustness table"})
        # The one of the same type that says nothing of it is not handed over.
        self.assertEqual([one["id"] for one in said["matches"]], [words["id"]])

    def test_the_type_counts_on_top_of_the_words(self) -> None:
        self._stage("evaluation", "checked the robustness table", medium="code")
        typed = self._stage("evaluation", "checked the robustness table",
                            medium="document")
        ids, said = self._ids({"kind": "evaluation", "type": "document",
                               "text": "robustness table"})
        self.assertEqual(said["matches"][0]["id"], typed["id"])
        self.assertIn("same type (document)", said["matches"][0]["why"])

    def test_the_place_in_its_card_counts_before_the_environment(self) -> None:
        a = self.store.add_card({"task_id": "t", "title": "a", "type": "document"})
        b = self.store.add_card({"task_id": "t", "title": "b", "type": "document"})
        self._stage("plan", "planned a", card=a["id"], began="2026-09-01")
        self._stage("execution", "drafted a", card=a["id"], began="2026-09-02")
        placed = self._stage("evaluation", "judged a", card=a["id"], began="2026-09-03")
        elsewhere = self._stage("evaluation", "judged b", card=b["id"], began="2026-09-04",
                                location="D:/papers/trade/main.tex")
        ids, said = self._ids({"kind": "evaluation", "type": "document",
                               "before": ["plan", "execution"],
                               "environment": ["env:ext:.tex"], "text": ""})
        self.assertEqual(ids[:2], [placed["id"], elsewhere["id"]])
        self.assertTrue(any(w.startswith("same place in its card: after plan → execution")
                            for w in said["matches"][0]["why"]))

    def test_then_the_environment(self) -> None:
        plain = self._stage("execution", "wrote the section", began="2026-09-05")
        tex = self._stage("execution", "wrote the section", began="2026-09-01",
                          location="D:/papers/trade/main.tex")
        ids, said = self._ids({"kind": "execution", "type": "document",
                               "environment": ["env:ext:.tex", "env:dir:trade"],
                               "text": "wrote the section"})
        self.assertEqual(ids[0], tex["id"])
        self.assertIn("same environment: trade, .tex", said["matches"][0]["why"])

    def test_a_rejected_attempt_is_a_place_of_its_own(self) -> None:
        card = self.store.add_card({"task_id": "t", "title": "c", "type": "document"})
        other = self.store.add_card({"task_id": "t", "title": "d", "type": "document"})
        self._stage("evaluation", "first look", card=card["id"], began="2026-09-01",
                    verdict="rejected")
        after_rejection = self._stage("adjustment", "changed it", card=card["id"],
                                      began="2026-09-02")
        self._stage("evaluation", "first look", card=other["id"], began="2026-09-03",
                    verdict="accepted")
        after_acceptance = self._stage("adjustment", "changed it", card=other["id"],
                                       began="2026-09-04")
        ids, _said = self._ids({"kind": "adjustment", "before": ["evaluation:rejected"],
                                "text": ""})
        self.assertEqual(ids[0], after_rejection["id"])
        self.assertLess(ids.index(after_rejection["id"]), ids.index(after_acceptance["id"]))


class OnlyShownWorkIsLearntFromTests(_Base):

    def test_a_hidden_task_gets_no_stages_and_is_not_offered(self) -> None:
        shown = self._task("task-shown", self._states(["a"] * 9), "Real work")
        hidden = Task(id="task-hidden", members=self._states(["b"] * 9),
                      described={"title": "Watching videos", "concrete": False})
        Kept(self.app.db).save([hidden])
        Projects(self.app.db).save(Project(id="project-1", name="P",
                                           tasks=["task-shown", "task-hidden"]))
        Projects(self.app.db).save(Project(id="project-2", name="Q",
                                           tasks=["task-hidden"]))
        self.experience.is_shown = lambda task: (
            (task.described or {}).get("concrete") is not False)
        units = {unit["id"]: unit for unit in self.experience.units()}
        self.assertIn("project-1", units)
        self.assertEqual([t["id"] for t in units["project-1"]["tasks"]],
                         ["task-shown"])
        self.assertNotIn("project-2", units)
        self.assertNotIn("task-hidden", units)
        self.assertIsNone(self.experience.unit("task-hidden"))

    def test_the_state_heartbeat_decides_what_is_shown(self) -> None:
        # Too little work is not shown either.
        short = Task(id="task-short", members=self._states(["c"] * 2),
                     described={"title": "An errand"})
        self.assertFalse(self.experience.is_shown(short))


class WhatIsLeftOpenWaitsTests(_Base):

    def test_an_open_tail_alone_is_not_asked_about_again(self) -> None:
        ids = self._states(["one", "two", "three"])
        task = self._task("task-a", ids, "A task")
        writer = self.experience.stages_writer()
        writer.models = _Models({"new_stages": [], "still_open": ids})
        unit = writer.units([task], [])[0]
        writer.extend(unit)
        self.assertEqual(writer.pending(unit), [], "asked again with nothing new")
        more = self._states(["four"])
        task.members = ids + more
        Kept(self.app.db).save([task])
        unit = writer.units([task], [])[0]
        # Something new: the open states come back with it.
        self.assertEqual(sorted(writer.pending(unit)), sorted(ids + more))


class PatternsAreBuiltBesideTheRoundTests(_Base):

    def test_they_run_in_a_thread_of_their_own_one_at_a_time(self) -> None:
        import threading
        import time

        release = threading.Event()
        entered = threading.Event()

        def slow(going, budget):
            entered.set()
            release.wait(5)
            return {"calls": 0}

        self.experience._regroup_if_due = slow
        self.assertTrue(self.experience.start_patterns())
        self.assertTrue(entered.wait(5), "the patterns did not start")
        # While one is running, another is not started.
        self.assertFalse(self.experience.start_patterns())
        self.assertTrue(self.experience.patterns_progress.get("patterns_running"))
        release.set()
        for _ in range(100):
            if not self.experience.patterns_progress.get("patterns_running"):
                break
            time.sleep(0.02)
        self.assertFalse(self.experience.patterns_progress.get("patterns_running"))
        self.assertTrue(self.experience.start_patterns())


class APatternRoundIsFrozenWhenItOpensTests(PatternsAndPrinciplesTests):

    def test_a_resumed_round_keeps_its_cutoff(self) -> None:
        import time
        from personalized_agent.data_manage.library import USER_NATURE, Record

        self._records()
        xp = self.experience
        xp.models = None
        # Opened, and stopped after one call.
        xp._regroup_now = True
        builder_calls = []

        def patterns_builder(going=None, _make=xp.patterns_builder, **kw):
            builder = _make(going, **kw)
            builder.models = self._model()
            builder_calls.append(builder)
            return builder

        xp.patterns_builder = patterns_builder
        first = xp._regroup_if_due(lambda: True, budget=1)
        self.assertTrue(first.get("unfinished"))
        held = self.app.config.get("experience_patterns_round")
        self.assertTrue(held.get("until"))
        # Written while the round is open: not in it.
        time.sleep(0.01)
        self.app.library.write(Record(folder=USER_NATURE, source="heartbeat",
            metadata={"nature_kind": "work"},
                                      title="The user plans the week on Sunday.",
                                      summary="The user plans the week on Sunday."))
        second = xp._regroup_if_due(lambda: True, budget=80)
        self.assertEqual(second["until"], held["until"])
        self.assertEqual(second["work"]["records"], first["work"]["records"])
        self.assertFalse(second.get("unfinished"), second)
        # Closed - and the record written meanwhile is the next round's.
        self.assertEqual(self.app.config.get("experience_patterns_round"), {})
        self.assertEqual(self.app.config.get("experience_grouped_until"),
                         held["until"])
        third = xp._regroup_if_due(lambda: True, budget=80)
        self.assertIn("1 new record", third.get("skipped", ""))


class AStagePassIsFrozenWhenItOpensTests(_Base):

    def test_states_written_during_a_pass_wait_for_the_next(self) -> None:
        import time

        ids = self._states(["one", "two"])
        task = self._task("task-a", ids, "A task")
        xp = self.experience
        xp._stage_pass(open_one=True)
        held = self.app.config.get("experience_stages_pass")
        self.assertTrue(held.get("until"))
        time.sleep(0.01)
        late = self._states(["written after the pass opened"])
        task.members = ids + late
        Kept(self.app.db).save([task])
        writer = xp.stages_writer()
        unit = writer.units([task], [])[0]
        self.assertEqual(writer.pending(unit), ids)
        # Resumed: the same cutoff, the same states.
        again = xp.stages_writer()
        self.assertEqual(again.until, held["until"])
        writer.models = _Models({"new_stages": [
            {"ref": "n1", "kind": "execution", "card": "root",
             "summary": "both", "states": ids}], "still_open": []})
        writer.extend(unit)
        self.assertTrue(xp._close_stage_pass_if_done(writer, [task], []))
        # Closed, it keeps its cutoff and says it is done.
        self.assertEqual(self.app.config.get("experience_stages_pass"),
                         {"until": held["until"], "done": True})
        # The next pass takes the late state.
        xp._stage_pass(open_one=True)
        self.assertEqual(xp.stages_writer().pending(unit), late)


class TwoKindsOfNatureTests(_Base):

    def _write(self, title, kind=None):
        from personalized_agent.data_manage.library import USER_NATURE, Record

        return self.app.library.write(Record(
            folder=USER_NATURE, title=title, summary=title, source="heartbeat",
            metadata={"nature_kind": kind} if kind else {}))

    def test_old_records_are_sorted_and_old_patterns_carried_over(self) -> None:
        from personalized_agent.heartbeat.experience.patterns import nature_records

        work = [self._write(f"The user wants slides with one idea each {i}.")
                for i in range(3)]
        rest = self._write("The user watches video between runs.")
        both = self._write("The user talks a design through before any code.")
        old = self.store.save_pattern({"layer": "pattern", "statement": "Old words",
                                       "members": [r.id for r in work + [rest, both]],
                                       "support": 5})
        self.store.save_pattern({"layer": "principle", "statement": "Old rule",
                                 "members": [old["id"]], "support": 5})

        def answer(request):
            ids = re.findall(r"\[(rec[^\]]*)\]", request.messages[0]["content"])
            said = {one: "work" for one in ids}
            said[rest.id], said[both.id] = "personal", "both"
            return {"kinds": said}

        sorter = self.experience.sorter()
        builder = self.experience.patterns_builder()
        builder.models = _Models(answer)
        sorter = self.experience.sorter(builder)
        said = sorter.sort(nature_records(self.app.library), budget=5)
        self.assertEqual(said["left"], 0, said)
        self.assertEqual(self.store.nature_kinds()[both.id], "both")
        sorter.carry_over()
        [pattern] = self.store.patterns("pattern", kind="work")
        # Its words kept; the personal record left it; the one that is both
        # stays, and is grouped in the personal kind as well.
        self.assertEqual(pattern["statement"], "Old words")
        self.assertNotIn(rest.id, pattern["members"])
        self.assertIn(both.id, pattern["members"])
        self.assertEqual(len(self.store.patterns("principle", kind="work")), 1)
        self.assertEqual(self.store.patterns(kind=""), [])

    def test_retrieval_reads_work_nature_only(self) -> None:
        work = self._write("The user wants every chart labelled in English.", "work")
        mine = self._write("The user likes charts of football results.", "personal")
        both = self._write("The user reads a chart before its caption.", "both")
        # Found through the stage that showed them.
        stage = self.store.add_stage({"task_id": "t", "kind": "execution",
                                      "summary": "made a chart of the results",
                                      "states": [], "began": "1", "ended": "1"})
        for record in (work, mine, both):
            self.store.link_stage_nature(stage["id"], record.id)
        said = self.experience.retriever().retrieve("make a chart")
        found = {rule["id"] for rule in said["rules"]}
        self.assertNotIn(mine.id, found)
        self.assertTrue({work.id, both.id} & found, said["rules"])


class ARoundIsTimedByItsPartsTests(unittest.TestCase):

    def test_parts_at_once_count_once(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _add_part

        held = {"visits": 0, "started_at": "x"}
        _add_part(held, "tasks", 0, 100, {"calls": 3, "tokens_in": 1000,
                                         "tokens_out": 100}, seconds=80)
        _add_part(held, "stages", 80, 100, {"calls": 1, "tokens_in": 50,
                                           "tokens_out": 5})
        _add_part(held, "work", 10, 160, {"calls": 4, "tokens_in": 400,
                                         "tokens_out": 40})
        _add_part(held, "personal", 200, 230, {"calls": 1, "tokens_in": 10,
                                              "tokens_out": 1})
        # 0-160 and 200-230: 190 seconds, not the parts' 80+20+150+30.
        self.assertEqual(held["seconds"], 190)
        self.assertEqual(held["tokens_in"], 1460)
        self.assertEqual(held["calls"], 9)
        self.assertEqual(held["parts"]["tasks"]["seconds"], 80)
        self.assertEqual(held["parts"]["work"]["seconds"], 150)


class TheKindsAreBuiltBeforeEverythingIsSortedTests(_Base):

    def test_old_patterns_split_once_most_records_have_a_kind(self) -> None:
        from personalized_agent.data_manage.library import USER_NATURE, Record

        known = [self.app.library.write(Record(
            folder=USER_NATURE, source="heartbeat",
            title=f"The user wants each slide to carry one idea {i}.",
            summary="slides", metadata={"nature_kind": "work"})) for i in range(3)]
        unsorted = self.app.library.write(Record(
            folder=USER_NATURE, source="heartbeat",
            title="The user wants slides with few words.", summary="slides"))
        self.store.save_pattern({"layer": "pattern", "statement": "Old words",
                                 "members": [r.id for r in known + [unsorted]],
                                 "support": 4})
        xp = self.experience
        xp.models = None               # nothing can be sorted this round
        xp._regroup_now = True
        said = xp._regroup_if_due(lambda: True, budget=5)
        # Split by kind already - the one not yet sorted left out, for later.
        [pattern] = self.store.patterns("pattern", kind="work")
        self.assertEqual(pattern["statement"], "Old words")
        self.assertNotIn(unsorted.id, pattern["members"])
        self.assertEqual(self.store.patterns(kind=""), [])
        # And the round stays open until it is sorted.
        self.assertIn("records still to sort", said.get("unfinished", ""))
        self.assertTrue(self.app.config.get("experience_patterns_round"))


class AStageRecordsTheWorkTests(_Base):

    def test_the_activity_is_shown_and_the_work_is_kept(self) -> None:
        from personalized_agent.common.events.models import ListenerEvent
        from personalized_agent.heartbeat.experience.stages import STAGE_PROMPT

        from personalized_agent.agent.states.store import State

        ids = self._states(["asked for the tree"])
        state = self.app.states.get(ids[0])
        said = ListenerEvent(
            event_type="assistant.message", source="assistant",
            timestamp=state.starts_at + timedelta(minutes=1),
            data={"role": "user", "tool": "Claude Code", "session_id": "7d1e5b20-x",
                  "project": r"C:\work\agent",
                  "text": "先放日期，再放地点; 手机上要能读"})
        harness = ListenerEvent(
            event_type="assistant.message", source="assistant",
            timestamp=state.starts_at + timedelta(minutes=2),
            data={"role": "user", "tool": "Claude Code", "session_id": "7d1e5b20-x",
                  "text": "Another Claude session sent a message: a report"})
        opened = ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            timestamp=state.starts_at + timedelta(minutes=3),
            data={"path": r"C:\work\agent\app.js"})
        browsed = ListenerEvent(
            event_type="browser.navigation", source="browser",
            timestamp=state.starts_at + timedelta(minutes=4),
            data={"url": "https://video.example/watch", "title": "a video"})
        for event in (said, harness, opened, browsed):
            self.app.raw_store.add_event(event)
        # A second state whose evidence cites the file it saved.
        cites = self.app.states.add(State(
            title="tried the tree", detail="saved app.js and looked",
            starts_at=state.starts_at + timedelta(minutes=3),
            ends_at=state.starts_at + timedelta(minutes=8),
            metadata={"evidence": [f"saved app.js ({opened.id})"]}))
        ids.append(cites.id)
        task = self._task("task-a", ids, "How you work as a tree")
        # Their materials, as step 1 links them when it writes the states.
        from personalized_agent.heartbeat.experience.materials import Materials
        materials = Materials(self.app.db, Path(self.app.library.root) / "references")
        window = {"from": (state.starts_at - timedelta(minutes=1)).isoformat(),
                  "to": (state.starts_at + timedelta(minutes=9)).isoformat()}
        materials.link(state, [said.id, harness.id], window)
        materials.link(cites, [], window)

        writer = self.experience.stages_writer()
        writer.models = _Models({"new_stages": [
            {"ref": "n1", "kind": "plan", "medium": "code",
             "card": "root", "summary": "Planned the tree.",
             "decision": {"about": "what the tree shows", "chose": "patterns",
                          "by": "agent"},
             "states": ids[:1]},
            {"ref": "n2", "kind": "execution", "medium": "code",
             "card": "root", "summary": "Built the tree.",
             "input": [{"what": "the file the user pointed the agent at",
                        "from": "user", "material": opened.id},
                       {"what": "the plan for what the tree shows",
                        "from": "stage", "stage": "n1"},
                       {"what": "", "from": "user"}],
             "decision": {"about": "how many layers the tree has",
                          "options": ["three layers", "two layers"],
                          "chose": "two layers", "by": "user",
                          "why": "先放日期，再放地点",
                          "rejected": "a third layer of records - too deep"},
             "output": {"what": "a two-layer tree",
                        "specification": [
                            {"requirement": "readable in dark mode",
                             "source": "the user checked: the tree in dark mode"},
                            {"requirement": "fast", "source": ""}],
                        "design": "principles as nodes that open onto patterns",
                        "rationale": {"why": "a third layer was too deep to read",
                                      "from": "user"},
                        "example": {"type": "code", "excerpt": "the tree"}},
             "references": [{"material": opened.id,
                             "what": "the tree drawing"},
                            {"kind": "file", "location": "",
                             "what": "nothing to open: dropped"}],
             "states": ids[1:]}], "still_open": []})
        writer.extend(writer.units([task], [])[0])
        sent = writer.models.asked[0].messages[0]["content"]
        [prompt] = self.store.prompts("task-a")
        self.assertEqual((prompt["kind"], prompt["messages"][0]["content"]),
                         ("states", sent))
        # The states and their materials, and nothing searched for: the
        # user's own words, the file a state cites - not the harness's
        # message, not a page no state linked.
        self.assertIn("先放日期", sent)
        self.assertIn(f"material [{said.id}] message", sent)
        self.assertIn(f"material [{opened.id}] file", sent)
        self.assertNotIn("Another Claude session", sent)
        self.assertNotIn("video.example", sent)
        # And the stage keeps the work, with each reference's location.
        planned, stage = self.store.stages(["task-a"])
        work = stage["work"]
        # Input, one main decision and who made it, output.
        self.assertEqual([one["what"] for one in work["input"]],
                         ["the file the user pointed the agent at",
                          "the plan for what the tree shows"])
        self.assertEqual(work["input"][1]["stage"], planned["id"])
        # An input names where it can be opened, and is pinned down like a
        # reference is: the material its state opened, found whole.
        self.assertEqual(work["input"][0]["material"], opened.id)
        self.assertEqual(work["input"][0]["location"], r"C:\work\agent\app.js")
        self.assertIn("EVERY INPUT POINTS AT WHERE IT CAN BE OPENED", STAGE_PROMPT)
        decided = work["decision"]
        self.assertEqual((decided["chose"], decided["by"]), ("two layers", "user"))
        self.assertEqual(decided["options"], ["three layers", "two layers"])
        self.assertIn("先放日期", decided["why"])
        self.assertEqual(planned["work"]["decision"]["by"], "agent")
        # Only what the user set is a specification.
        self.assertEqual([one["requirement"] for one in work["output"]["specification"]],
                         ["readable in dark mode"])
        self.assertEqual(work["output"]["rationale"],
                         {"why": "a third layer was too deep to read", "from": "user"})
        self.assertEqual(stage["deliverable"]["excerpt"], "the tree")
        # The input from the plan is an edge to it.
        self.assertIn((stage["id"], planned["id"]),
                      {(e["source"], e["target"]) for e in
                       self.store.edges([stage["id"]])})
        self.assertIn("ONE MAIN DECISION", STAGE_PROMPT)
        self.assertIn("NO LABEL FROM THE WORK ITSELF", STAGE_PROMPT)
        self.assertEqual([r["location"] for r in work["references"]],
                         [r"C:\work\agent\app.js"])
        self.assertEqual(work["references"][0]["kind"], "file")
        # Told not to write how long, or whether a session was alive.
        self.assertIn("NEVER write how long it took", STAGE_PROMPT)
        # Who made the call, always.
        self.assertIn("WHO ACTUALLY MADE THE CALL", STAGE_PROMPT)
        # The logic of the work, never the rhythm of the user's day.
        self.assertIn("THE LOGIC OF THE WORK, NOT THE RHYTHM OF THE DAY",
                      STAGE_PROMPT)
        self.assertNotIn("working_together", STAGE_PROMPT)
        # Each stage read alone: no other stage's ref, no loose pronouns.
        self.assertIn("NEVER NAME ANOTHER STAGE OR CARD BY ITS ID OR REF", STAGE_PROMPT)


class AStageFindsItsNaturesDirectlyTests(_Base):

    def test_retrieval_goes_stage_to_nature(self) -> None:
        from personalized_agent.data_manage.library import USER_NATURE, Record
        stage = self.store.add_stage({"task_id": "t", "kind": "evaluation",
                                      "summary": "tried the slides on the projector",
                                      "states": [], "began": "1", "ended": "1"})
        record = self.app.library.write(Record(
            folder=USER_NATURE, title="The user wants one idea a slide.",
            summary="x", source="experience", metadata={"nature_kind": "work"}))
        self.store.link_stage_nature(stage["id"], record.id)
        # Two words in common - one is no match (retrieve.MIN_SHARED_WORDS).
        said = self.experience.retriever().retrieve("check the slides on the projector", seeds=1)
        found = {rule["id"] for rule in said["rules"] if rule["kind"] == "nature"}
        self.assertIn(record.id, found)


class AStageCarriesItsNaturesTests(_Base):

    def test_a_stage_shows_its_natures_and_their_patterns(self) -> None:
        from personalized_agent.data_manage.library import USER_NATURE, Record
        stage = self.store.add_stage({"task_id": "t", "kind": "evaluation",
                                      "summary": "tried the slides on the projector",
                                      "states": [], "began": "1", "ended": "1"})
        other = self.store.add_stage({"task_id": "t", "kind": "execution",
                                      "summary": "made the slides",
                                      "states": [], "began": "0", "ended": "0"})
        record = self.app.library.write(Record(
            folder=USER_NATURE, title="The user judges a slide by one idea on it.",
            summary="x", source="experience", metadata={"nature_kind": "work"}))
        self.store.link_stage_nature(stage["id"], record.id)
        self.store.set_nature_kind(record.id, "work")
        self.store.save_pattern({"layer": "pattern", "statement": "Slides carry one idea",
                                 "members": [record.id], "nature_kind": "work"})
        said = {s["id"]: s for s in self.experience._with_natures([stage, other])}
        self.assertEqual([n["title"] for n in said[stage["id"]]["natures"]],
                         ["The user judges a slide by one idea on it."])
        self.assertEqual(said[stage["id"]]["natures"][0]["kind"], "work")
        self.assertEqual(said[stage["id"]]["natures"][0]["pattern"], "Slides carry one idea")
        self.assertEqual(said[other["id"]]["natures"], [])


class ThePictureCarriesTheStagesAloneTests(_Base):
    """A project's picture reads no nature record - while a project is
    learned they change, and each picture read them all again; a stage's
    natures are asked for when its card is opened."""

    def test_natures_come_with_the_card_not_the_picture(self) -> None:
        from unittest import mock
        from personalized_agent.data_manage.library import USER_NATURE, Record
        stage = self.store.add_stage({"task_id": "t", "kind": "evaluation",
                                      "summary": "tried the slides on the projector",
                                      "states": [], "began": "1", "ended": "1"})
        record = self.app.library.write(Record(
            folder=USER_NATURE, title="Judges a slide by one idea on it.",
            summary="x", source="experience", metadata={"nature_kind": "work"}))
        self.store.link_stage_nature(stage["id"], record.id)
        unit = {"id": "t", "kind": "task", "name": "Slides", "tasks": []}
        with mock.patch.object(self.experience, "_shown_units", return_value=[unit]),                 mock.patch("personalized_agent.heartbeat.experience.service.nature_records",
                           side_effect=AssertionError("read the natures")):
            graph = self.experience.graph("t")
        self.assertEqual([one["id"] for one in graph["stages"]], [stage["id"]])
        self.assertNotIn("natures", graph["stages"][0])
        said = self.experience.stage_detail(stage["id"])
        self.assertEqual([one["title"] for one in said["natures"]],
                         ["Judges a slide by one idea on it."])
        self.assertIn("words", said["found_by"])
        self.assertIsNone(self.experience.stage_detail("stage_nowhere"))


class ARoundsStagesUseItsCutoffTests(_Base):

    def test_the_stage_pass_is_the_rounds(self) -> None:
        cutoff = "2026-02-02T00:00:00+00:00"
        self.experience.models = _Models({"new_stages": [], "still_open": []})
        report = self.experience.after_round(budget=1, until=cutoff)
        self.assertEqual(self.experience._stage_pass()["until"], cutoff)
        # Nothing to write: caught up, so the round is not held open.
        self.assertTrue(report["pass_closed"])
        again = self.experience.after_round(budget=1, until=cutoff)
        self.assertTrue(again["pass_closed"])
        self.assertFalse(again.get("progress"))


class _Spending(_Models):
    """A model that says what each answer cost."""

    def invoke(self, request, role=""):
        reply = super().invoke(request, role)

        class Usage:
            input_tokens, output_tokens = 1000, 100

        reply.usage = Usage()
        return reply


class LearningProjectByProjectTests(_Base):
    """Each project is learned when the user starts it - and updated when
    they ask - with what it has cost on its own account. Several at once;
    nothing learned is ever cleared. The state heartbeat writes no stages."""

    def _finished_round(self, until):
        config = self.app.config
        config.set("state_round_started_at", until, internal=True)
        config.set("state_round_reads_until", until, internal=True)
        config.set("state_round_done", True, internal=True)

    def _wait(self, xp, units, seconds=60):
        import time
        deadline = time.time() + seconds
        while time.time() < deadline:
            mine = [xp.learning_progress.get(one) or {} for one in units]
            if all(not one.get("running") and not one.get("queued") for one in mine):
                return
            time.sleep(0.1)
        self.fail("learning did not finish")

    def _a_task(self, task_id, titles, summary):
        ids = self._states(titles)
        self._task(task_id, ids, task_id)
        return ids

    def _learner(self, summary):
        def answer(request):
            if "TURN THIS PIECE" not in (request.system or ""):
                return {}
            found = re.findall(r"- \[(state_[0-9a-f]+)\]", request.messages[0]["content"])
            return {"new_stages": [{"ref": "n1", "kind": "execution", "card": "root",
                                    "summary": summary, "states": found}],
                    "still_open": []}
        return _Spending(answer)

    def test_a_round_leaves_no_stages_to_write(self) -> None:
        beat = self.app.state_heartbeat
        beat.models = beat.experience.models = _Models({"new_stages": [],
                                                         "still_open": []})
        self._finished_round("2026-03-03T00:00:00+00:00")
        beat._how_much_is_blank = lambda: 0
        self.assertNotIn("stages", beat._steps_left())
        said = beat.run_if_due(trigger="timer")
        self.assertNotIn("stages", said.get("carried_on") or [])

    def test_learning_writes_its_stages_and_counts_what_it_cost(self) -> None:
        self._a_task("task-a", ["asked for rows", "built them"], "Rows")
        xp = self.experience
        xp.is_shown = lambda task: True
        xp.models = self._learner("built the rows")
        self.assertEqual(xp.learn(["task-a"]), ["task-a"])
        self._wait(xp, ["task-a"])
        self.assertEqual([s["summary"] for s in self.store.stages(["task-a"])],
                         ["built the rows"])
        said = xp.learning_of("task-a")
        self.assertTrue(said["learned"])
        self.assertTrue(said["done"])
        self.assertEqual(said["tokens"], said["calls"] * 1100)
        self.assertGreater(said["calls"], 0)
        # Counted as the experience's, not the state heartbeat's - and as
        # learning the user started, apart from the natures grouped by itself.
        self.assertEqual({one.task for one in xp.models.asked}, {"experience_stages"})

    def test_updating_reads_what_is_new_and_adds_to_what_it_cost(self) -> None:
        ids = self._a_task("task-a", ["one"], "A")
        xp = self.experience
        xp.is_shown = lambda task: True
        xp.models = self._learner("first")
        xp.learn(["task-a"])
        self._wait(xp, ["task-a"])
        before = xp.learning_of("task-a")["tokens"]
        more = self._states(["two"])
        self._task("task-a", ids + more, "A")
        xp.models = self._learner("second")
        xp.learn(["task-a"])
        self._wait(xp, ["task-a"])
        self.assertEqual([s["summary"] for s in self.store.stages(["task-a"])],
                         ["first", "second"])
        self.assertGreater(xp.learning_of("task-a")["tokens"], before)

    def test_an_update_after_a_stop_carries_on_to_now(self) -> None:
        """No resuming under an old cutoff: an update reads up to now, from
        wherever the last learning stopped."""
        from datetime import datetime, timezone
        self._a_task("task-a", ["one"], "A")
        xp = self.experience
        xp.is_shown = lambda task: True
        self.store.save_learning("task-a", until="2026-01-01T00:00:00+00:00", done=0)
        xp.models = self._learner("one")
        began = datetime.now(timezone.utc).isoformat()
        xp.learn(["task-a"])
        self._wait(xp, ["task-a"])
        self.assertGreaterEqual(xp.learning_progress["task-a"]["until"], began)
        self.assertNotIn("resumed", xp.learning_progress["task-a"])
        self.assertTrue(xp.learning_of("task-a")["done"])

    def test_several_are_learned_at_once(self) -> None:
        self._a_task("task-a", ["a"], "A")
        self._a_task("task-b", ["b"], "B")
        xp = self.experience
        xp.is_shown = lambda task: True
        xp.models = self._learner("learned")
        self.assertEqual(sorted(xp.learn(["task-a", "task-b"])), ["task-a", "task-b"])
        self._wait(xp, ["task-a", "task-b"])
        self.assertTrue(xp.learning_of("task-a")["done"])
        self.assertTrue(xp.learning_of("task-b")["done"])
        # Asked again while one is going, it is not started twice.
        xp.learning_progress["task-a"] = {"running": True}
        self.assertEqual(xp.learn(["task-a"]), [])

    def test_a_project_made_for_the_agents_is_not_learned(self) -> None:
        made = self.store.create_work_project("Notes")
        self.assertEqual(self.experience.learn([made["unit_id"]]), [])


class WhatTheTestLearnedBecomesTheUsersTests(unittest.TestCase):
    """Once, at start: the Build now test's stages and natures become the
    user's experience, the old lessons go, everything is backed up first."""

    def setUp(self) -> None:
        import tempfile
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.internal = Path(tempfile.mkdtemp(prefix="pa-internal-"))
        self.store = ExperienceStore(self.app.db)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.internal, ignore_errors=True)
        self.agent.cleanup()

    def _record(self, folder, record_id, source):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{record_id}.md").write_text(
            f"---\nid: {record_id}\ntitle: t\nsource: {source}\n---\n\nbody",
            encoding="utf-8")

    def test_it_is_done_once_and_keeps_who_the_user_is(self) -> None:
        from personalized_agent.heartbeat.experience.promote import promote_the_test
        from personalized_agent.heartbeat.experience.service import scoped_key
        natures = Path(self.app.library.root) / "user_nature"
        old = self.store.add_stage({"task_id": "task-a", "kind": "plan",
                                    "summary": "an old lesson", "states": [],
                                    "began": "1", "ended": "1"})
        self._record(natures, "rec_old", "experience")
        self.store.link_stage_nature(old["id"], "rec_old")
        self._record(natures, "rec_who", "heartbeat")
        self._record(natures, "rec_dh", "data_heartbeat")
        test = ExperienceStore(self.app.db, "build")
        test.add_stage({"task_id": "task-a", "kind": "execution",
                        "summary": "the test's lesson", "states": [],
                        "began": "2", "ended": "2"})
        self._record(self.internal / "experience_build" / "user_nature", "rec_new",
                     "experience")
        self.app.config.set(scoped_key("build", "experience_run"),
                            {"until": "2026-10-06T00:00:00+00:00", "done": True},
                            internal=True)
        said = promote_the_test(self.app.db, self.app.config,
                                Path(self.app.library.root), self.internal)
        self.assertEqual([s["summary"] for s in self.store.stages()],
                         ["the test's lesson"])
        self.assertEqual(ExperienceStore(self.app.db, "build").stages(), [])
        names = {one.name for one in natures.glob("*.md")}
        self.assertEqual(names, {"rec_who.md", "rec_new.md"})
        # Backed up, whole; the retired records put away, not deleted.
        backup = Path(said["backup"])
        self.assertTrue((backup / "user_nature" / "rec_old.md").exists())
        self.assertTrue((backup / "retired" / "user_nature" / "rec_dh.md").exists())
        self.assertFalse((self.internal / "experience_build").exists())
        # Learned, up to the test's cutoff.
        self.assertEqual(self.store.learning("task-a")["learned_at"],
                         "2026-10-06T00:00:00+00:00")
        # Once.
        self.assertEqual(promote_the_test(self.app.db, self.app.config,
                                          Path(self.app.library.root), self.internal), {})


class PatternsKnowWhenTheyWereLastActiveTests(_Base):
    """A pattern is last active when the latest of its natures was seen in
    the user's work - through the stages it was written from - and a
    principle when the latest of its patterns was. The reviewer is told."""

    def _nature(self, title, stage_ended=None):
        from personalized_agent.data_manage.library import USER_NATURE, Record
        record = self.app.library.write(Record(
            folder=USER_NATURE, title=title, summary="s", source="experience",
            metadata={"nature_kind": "work"}))
        if stage_ended:
            stage = self.store.add_stage({"task_id": "t", "kind": "evaluation",
                                          "summary": title, "states": [],
                                          "began": stage_ended, "ended": stage_ended})
            self.store.link_stage_nature(stage["id"], record.id)
        return record

    def test_the_rules_come_whole_with_what_shows_them_and_first(self) -> None:
        """Each nature with up to five of its cases, newest first; a pattern
        with its natures; a principle with its patterns - and the rules
        before the stages, never cut: what does not fit is the stages."""
        import json as _json
        from personalized_agent.agent.work.tools import compact_experience, experience_text
        nature = self._nature("checks it in dark mode", "2026-09-30T10:00:00+00:00")
        cases = [{"said": f"case {n}", "what": f"look {n}", "stage": f"stage_{n}",
                  "at": f"2026-09-0{n}T10:00:00+00:00"} for n in range(1, 8)]
        nature.metadata["examples"] = _json.dumps(cases)
        self.app.library.put(nature)
        pattern = self.store.save_pattern({"layer": "pattern", "statement": "Tries it",
                                           "detail": "D" * 900, "members": [nature.id],
                                           "support": 1, "nature_kind": "work"})
        self.store.save_pattern({"layer": "principle", "statement": "Judges by use",
                                 "members": [pattern["id"]], "support": 1,
                                 "nature_kind": "work"})
        said = compact_experience(self.experience.retriever().retrieve(
            "checks it in dark mode", kinds=["evaluation"]))
        natures = said["rules"]["natures"]
        self.assertEqual([one["said"] for one in natures[0]["examples"]],
                         ["case 7", "case 6", "case 5", "case 4", "case 3"])
        patterns = said["rules"]["patterns"]
        self.assertEqual(patterns[0]["detail"], "D" * 900, "whole, not cut")
        self.assertEqual(patterns[0]["examples"][0]["nature"], "checks it in dark mode")
        self.assertEqual(said["rules"]["principles"][0]["examples"],
                         [{"pattern": "Tries it"}])
        self.assertTrue(said["stages"] and "open_stage" in said["open"])
        # Too long: the stages go, the rules stay whole, and it is still JSON.
        rules_only = len(_json.dumps({"rules": said["rules"], "stages": [],
                                      "open": said["open"]}, ensure_ascii=False))
        text = experience_text(said, rules_only + 5)
        kept = _json.loads(text)
        self.assertEqual(kept["stages"], [])
        self.assertEqual(kept["rules"], said["rules"])

    def test_patterns_and_principles_take_their_latest(self) -> None:
        old = self._nature("checks it by hand", "2026-01-05T10:00:00+00:00")
        new = self._nature("checks it in dark mode", "2026-09-30T10:00:00+00:00")
        quiet = self._nature("writes long letters", "2025-06-01T10:00:00+00:00")
        a = self.store.save_pattern({"layer": "pattern", "statement": "Tries it",
                                     "members": [old.id, new.id], "support": 2,
                                     "nature_kind": "work"})
        b = self.store.save_pattern({"layer": "pattern", "statement": "Long letters",
                                     "members": [quiet.id], "support": 1,
                                     "nature_kind": "work"})
        self.store.save_pattern({"layer": "principle", "statement": "Judges by use",
                                 "members": [a["id"], b["id"]], "support": 3,
                                 "nature_kind": "work"})
        self.store.refresh_last_active(self.app.library)
        at = {p["statement"]: p["last_active"] for p in self.store.patterns()}
        self.assertEqual(at["Tries it"], "2026-09-30T10:00:00+00:00")
        self.assertEqual(at["Long letters"], "2025-06-01T10:00:00+00:00")
        self.assertEqual(at["Judges by use"], "2026-09-30T10:00:00+00:00")

    def test_a_principle_is_active_in_stretches_with_the_gaps_left(self) -> None:
        a = self._nature("one", "2026-09-01T10:00:00+00:00")
        b = self._nature("two", "2026-09-02T10:00:00+00:00")
        c = self._nature("three", "2026-09-20T10:00:00+00:00")
        pattern = self.store.save_pattern({"layer": "pattern", "statement": "p",
                                           "members": [a.id, b.id, c.id], "support": 3,
                                           "nature_kind": "work"})
        principle = self.store.save_pattern({"layer": "principle", "statement": "pr",
                                             "members": [pattern["id"]], "support": 3,
                                             "nature_kind": "work"})
        said = self.store.active_stretches(self.app.library)[principle["id"]]
        # The first two a day apart are one stretch; the third, weeks on, another.
        self.assertEqual([one[0][:10] for one in said], ["2026-09-01", "2026-09-20"])
        self.assertEqual(said[0][1][:10], "2026-09-02")

    def test_a_nature_opens_whole(self) -> None:
        nature = self._nature("checks it in dark mode", "2026-09-30T10:00:00+00:00")
        pattern = self.store.save_pattern({"layer": "pattern", "statement": "Tries it",
                                           "members": [nature.id], "support": 1,
                                           "nature_kind": "work"})
        self.store.save_pattern({"layer": "principle", "statement": "Judges by use",
                                 "members": [pattern["id"]], "support": 1,
                                 "nature_kind": "work"})
        self.store.refresh_last_active(self.app.library)
        said = self.experience.nature(nature.id)
        self.assertEqual(said["pattern"]["statement"], "Tries it")
        self.assertEqual(said["principle"]["statement"], "Judges by use")
        self.assertEqual(said["principle"]["last_active"], "2026-09-30T10:00:00+00:00")
        self.assertEqual([s["summary"] for s in said["lessons"]], ["checks it in dark mode"])
        self.assertIsNone(self.experience.nature("rec_nowhere"))

    def test_the_reviewer_is_shown_when_each_was_last_active(self) -> None:
        nature = self._nature("checks it in dark mode", "2026-09-30T10:00:00+00:00")
        stage = self.store.natures_of_stages
        self.store.save_pattern({"layer": "pattern", "statement": "Tries it",
                                 "members": [nature.id], "support": 1,
                                 "nature_kind": "work"})
        self.app.state_heartbeat.experience.store.nature_kinds  # noqa: B018
        self.store.set_nature_kind(nature.id, "work", {})
        self.store.refresh_last_active(self.app.library)
        from personalized_agent.agent.work.tools import compact_experience
        said = compact_experience(self.experience.retriever().retrieve(
            "checks it in dark mode", kinds=["evaluation"]))
        rules = {r["statement"]: r for kind in said["rules"].values() for r in kind}
        self.assertEqual(rules["Tries it"]["last_active"], "2026-09-30T10:00:00+00:00")
        from personalized_agent.agent.work.runner import REVIEWER_SYSTEM
        self.assertIn("WEIGH A RULE BY WHEN IT WAS LAST ACTIVE", REVIEWER_SYSTEM)

    def test_learning_leaves_the_grouping_to_the_state_heartbeat(self) -> None:
        import time
        xp = self.experience
        xp.is_shown = lambda task: True
        ids = self._states(["one"])
        self._task("task-a", ids, "A")
        xp.models = _Models({"new_stages": [], "still_open": []})
        xp.start_patterns = lambda *a, **k: self.fail("learning started a grouping")
        xp.learn(["task-a"])
        for _ in range(300):
            if not (xp._learn_thread and xp._learn_thread.is_alive()):
                break
            time.sleep(0.05)


class TheUsersOwnProjectsTests(_Base):
    """Made with a title, a description and a folder; listed in the order
    the user puts them in."""

    def test_the_description_is_its_goal(self) -> None:
        import tempfile
        folder = tempfile.mkdtemp(prefix="pa-proj-")
        made = self.experience.create_project("The paper", [folder],
                                              "Revise it for the second round")
        unit = next(u for u in self.experience.directory_units()
                    if u["id"] == made["unit_id"])
        self.assertEqual(unit["name"], "The paper")
        self.assertEqual(unit["why"], "Revise it for the second round")

    def test_the_most_lately_active_first(self) -> None:
        import tempfile
        made = [self.experience.create_project(name, [tempfile.mkdtemp()])["unit_id"]
                for name in ("First", "Second", "Third")]
        # Read to: Third most lately, then First; Second never.
        for unit_id, until in ((made[0], "2026-10-10T09:00:00+00:00"),
                               (made[2], "2026-10-13T09:00:00+00:00")):
            self.app.db.execute("UPDATE experience_workspaces SET processed_until = ? "
                                "WHERE unit_id = ?", (until, unit_id))
        units = [u for u in self.experience.units() if u["id"] in made]
        self.assertEqual([u["id"] for u in units], [made[2], made[0], made[1]])
        self.assertEqual(units[0]["last_active"], "2026-10-13T09:00:00+00:00")
        self.assertEqual(units[2]["last_active"], "")


class OnlyTheLessonsNaturesAreKeptTests(WhatTheTestLearnedBecomesTheUsersTests):
    """Once, at start: every nature not from a lesson is taken out - backed
    up - with its kind, its links and its places in patterns."""

    def test_it_is_done_once_and_keeps_the_lessons(self) -> None:
        from personalized_agent.heartbeat.experience.promote import (
            keep_only_lesson_natures)
        natures = Path(self.app.library.root) / "user_nature"
        self._record(natures, "rec_lesson", "experience")
        self._record(natures, "rec_who", "heartbeat")
        self.store.set_nature_kind("rec_lesson", "work", {})
        self.store.set_nature_kind("rec_who", "personal", {})
        mixed = self.store.save_pattern({"layer": "pattern", "statement": "mixed",
                                         "members": ["rec_lesson", "rec_who"],
                                         "support": 2, "nature_kind": "work"})
        theirs = self.store.save_pattern({"layer": "pattern", "statement": "theirs",
                                          "members": ["rec_who"], "support": 1,
                                          "nature_kind": "personal"})
        self.store.save_pattern({"layer": "principle", "statement": "only theirs",
                                 "members": [theirs["id"]], "support": 1,
                                 "nature_kind": "personal"})
        said = keep_only_lesson_natures(self.app.db, self.app.config,
                                        Path(self.app.library.root), self.internal)
        self.assertEqual(said["natures_removed"], 1)
        self.assertEqual({one.name for one in natures.glob("*.md")}, {"rec_lesson.md"})
        self.assertTrue((Path(said["backup"]) / "user_nature" / "rec_who.md").exists())
        self.assertEqual(self.store.nature_kinds(), {"rec_lesson": "work"})
        left = {p["statement"]: p["members"] for p in self.store.patterns()}
        self.assertEqual(left, {"mixed": ["rec_lesson"]})
        self.assertEqual(keep_only_lesson_natures(
            self.app.db, self.app.config, Path(self.app.library.root), self.internal), {})


class TheLoopCarriesOnWhatTheRoundLeftTests(_Base):
    """A finished round's grouping of the natures, left open, is carried on
    by the minutely check - not left for the next round hours away."""

    def _finished_round(self, until):
        config = self.app.config
        config.set("state_round_started_at", until, internal=True)
        config.set("state_round_reads_until", until, internal=True)
        config.set("state_round_done", True, internal=True)

    def test_a_grouping_left_open_is_carried_on(self) -> None:
        beat = self.app.state_heartbeat
        beat.models = beat.experience.models = _Models({})
        until = "2026-03-03T00:00:00+00:00"
        self._finished_round(until)
        beat._how_much_is_blank = lambda: 0
        self.app.config.set("experience_patterns_round", {"until": until},
                            internal=True)
        # The people's round of it is done: only the grouping is left open.
        self.app.config.set("connections_round_until", until, internal=True)
        self.assertEqual(beat._steps_left(), ["patterns"])
        said = beat.run_if_due(trigger="timer")
        self.assertFalse(said.get("ran"), "not a new round")
        self.assertIn("patterns", said.get("carried_on") or [])


class TheTextPointsAtItsReferencesTests(unittest.TestCase):

    def test_the_example_is_named_by_its_references_ids(self) -> None:
        from personalized_agent.heartbeat.experience.stages import (
            _example_of, _work_of)
        work = _work_of({
            "input": [{"what": "the referee's comment [r1]", "from": "material",
                       "ref": "r1"}],
            "references": [{"id": "r1", "material": "event_a", "what": "the comment"},
                           {"id": "r2", "location": "D:/paper/main.tex",
                            "what": "the section"}]})
        self.assertEqual([one["id"] for one in work["references"]], ["r1", "r2"])
        self.assertEqual(work["input"][0]["ref"], "r1")
        made = _example_of({"type": "document", "references": ["r2", "r1", "r9"]},
                           work["references"])
        self.assertEqual(made["references"], [{"id": "r2",
                                               "location": "D:/paper/main.tex",
                                               "what": "the section"}])
        self.assertEqual(made["materials"], ["event_a"])
        from personalized_agent.heartbeat.experience.stages import STAGE_PROMPT
        self.assertIn("POINT AT THE REFERENCES", STAGE_PROMPT)



class ANatureShowsAllItsExamplesTests(_Base):
    """A nature, clicked, shows every case it was seen in - each lesson it
    was learned from or linked to - and what it said before it was
    rewritten."""

    def _stage(self, task_id, summary, began, card_title=""):
        card_id = ""
        if card_title:
            card_id = self.store.add_card({"unit_id": task_id, "task_id": task_id,
                                           "title": card_title, "statement": "s"})["id"]
        return self.store.add_stage({
            "task_id": task_id, "kind": "evaluation", "summary": summary,
            "states": [], "began": began, "ended": began, "card_id": card_id,
            "deliverable": {"excerpt": f"out of {summary}",
                            "references": [{"location": f"C:/w/{summary}.png",
                                            "what": "the picture"}]},
            "work": {"decision": {"about": f"about {summary}"},
                     "references": [{"id": "r1", "location": f"D:/paper/{summary}.tex",
                                     "what": "the section"}]}})

    def _nature(self):
        from personalized_agent.heartbeat.experience.stage_natures import write_nature
        self._task("task-a", [], "Write the paper")
        self._task("task-b", [], "Draw the figures")
        Projects(self.app.db).save(Project(id="project-1", name="The paper",
                                           tasks=["task-a"]))
        first = self._stage("task-a", "first", "2026-09-01T10:00:00+00:00")
        second = self._stage("task-b", "second", "2026-09-20T10:00:00+00:00")
        third = self._stage("task-a", "third", "2026-09-10T10:00:00+00:00",
                            card_title="The figures print")
        self.assertTrue(write_nature(
            self.app.library, self.store,
            {"title": "Judges a figure by printing it", "aspect": "evaluation",
             "content": "prints it", "example": {"what": "the first figure, printed",
                                                 "references": ["r1"]}},
            [first["id"], second["id"]], {"model": "m", "provider": "p"},
            [{"id": "r1", "location": "C:/w/print.pdf", "what": "the print"}]))
        record = next(r for r in self.app.library.records("user_nature")
                      if r.title == "Judges a figure by printing it")
        # A third lesson linked to it later, with no word in its record.
        self.store.link_stage_nature(third["id"], record.id)
        return record, first, second, third

    def test_every_linked_lesson_is_an_example_newest_first(self) -> None:
        record, first, second, third = self._nature()
        said = self.experience.nature(record.id)
        self.assertEqual(said["examples_total"], 3)
        self.assertEqual([e["id"] for e in said["examples"]],
                         [second["id"], third["id"], first["id"]])
        by_id = {e["id"]: e for e in said["examples"]}
        own = by_id[first["id"]]
        # The record's own example goes with the lesson it was written from,
        # its place first.
        self.assertTrue(own["own_example"])
        self.assertEqual(own["what"], "the first figure, printed")
        self.assertEqual(own["locations"][0]["location"], "C:/w/print.pdf")
        self.assertIn("D:/paper/first.tex", [one["location"] for one in own["locations"]])
        self.assertIn("C:/w/first.png", [one["location"] for one in own["locations"]])
        self.assertEqual(own["task"], "Write the paper")
        self.assertEqual(own["project"], "The paper")
        self.assertEqual(by_id[third["id"]]["title"], "The figures print")
        self.assertEqual(own["deliverable"], "out of first")
        self.assertEqual(own["began"][:10], "2026-09-01")
        other = by_id[second["id"]]
        self.assertFalse(other["own_example"])
        self.assertEqual(other["task"], "Draw the figures")
        self.assertEqual(other["project"], "")
        self.assertEqual(other["title"], "about second")
        self.assertEqual(said["history"], [])

    def test_a_rewritten_nature_keeps_what_it_said_before(self) -> None:
        from personalized_agent.heartbeat.experience.stage_natures import (
            history_of, revise_nature)
        record, first, _second, _third = self._nature()
        other = self.app.library.write(type(record)(
            folder="user_nature", title="Prints every figure", summary="s",
            source="experience"))
        late = self._stage("task-b", "late", "2026-10-01T10:00:00+00:00")
        revise_nature(self.app.library, record, title="Judges every figure on paper",
                      summary="prints them all", why="seen again", run="run_1",
                      merged_from=[other.id], stages=[late["id"]], store=self.store)
        again = self.app.library.get(record.id, "user_nature")
        revise_nature(self.app.library, again, summary="prints them all, twice",
                      why="reworded", run="run_2")
        last = self.app.library.get(record.id, "user_nature")
        kept = history_of(last)
        self.assertEqual([one["why"] for one in kept], ["reworded", "seen again"])
        self.assertEqual(kept[1]["title"], "Judges a figure by printing it")
        self.assertEqual(kept[0]["title"], "Judges every figure on paper")
        said = self.experience.nature(record.id)
        self.assertEqual(said["title"], "Judges every figure on paper")
        self.assertEqual([one["run"] for one in said["history"]], ["run_2", "run_1"])
        self.assertEqual(said["history"][1]["merged_from"],
                         [{"id": other.id, "title": "Prints every figure"}])
        # The lesson it now also rests on is one of its examples.
        self.assertEqual(said["examples_total"], 4)
        self.assertEqual(said["examples"][0]["id"], late["id"])
        # The original record read before is not changed by the rewrite.
        self.assertEqual(record.title, "Judges a figure by printing it")


class WhatIsFoundIsWhatIsAskedAboutTests(_Base):
    """A past stage is handed over for what it says - its words first, its
    likenesses on top of them - and only when it says enough of it; the
    words a Chinese sentence is held together by match nothing; the user's
    ways of judging are found by their own words too; and a stage handed
    over already in a run gives way to one not seen."""

    def _stage(self, summary, kind="evaluation", began="2026-09-01T10:00:00+00:00"):
        return self.store.add_stage({"task_id": "t", "kind": kind, "summary": summary,
                                     "states": [], "began": began, "ended": began})

    def _nature(self, title, summary="s"):
        from personalized_agent.data_manage.library import USER_NATURE, Record
        return self.app.library.write(Record(
            folder=USER_NATURE, title=title, summary=summary, source="experience",
            metadata={"nature_kind": "work"}))

    def test_words_that_hold_a_sentence_together_match_nothing(self) -> None:
        from personalized_agent.heartbeat.experience.index import terms_of
        self.assertEqual(terms_of("早上可以这个每个最终"), ["早上"])
        # Nor is a pair made across one.
        self.assertNotIn("上可", terms_of("早上可以"))
        self.assertEqual(terms_of("我们需要做一个预约页面")[:3], ["预约", "约页", "页面"])

    def test_only_what_matches_is_handed_over(self) -> None:
        wanted = self._stage("checked the booking checkout against the payment limits")
        self._stage("reworded the panel title", kind="evaluation")
        self._stage("moved a button on the settings page", kind="evaluation")
        found = self.experience.retriever().find(
            {"kind": "evaluation", "text": "booking payment limits"})
        self.assertEqual([one["id"] for one in found["matches"]], [wanted["id"]])
        nothing = self.experience.retriever().find(
            {"kind": "evaluation", "text": "astronomy telescope"})
        self.assertEqual(nothing["matches"], [])
        self.assertIn("none_close", nothing)

    def test_the_words_come_before_the_kind(self) -> None:
        about = self._stage("planned the booking payment flow", kind="plan")
        self._stage("judged the colour of the header", kind="evaluation")
        found = self.experience.retriever().find(
            {"kinds": ["evaluation", "plan"], "text": "booking payment"})
        self.assertEqual(found["matches"][0]["id"], about["id"])

    def test_a_stage_seen_already_in_the_run_keeps_its_place(self) -> None:
        """A stage given before no longer gives way (2026-10-17): each
        reviewer's call is new and holds none of what an earlier one was given."""
        self._stage("tested the booking payment refunds")
        self._stage("tested the booking payment receipts flow")
        profile = {"kind": "evaluation", "text": "booking payment"}
        ranked = [one["id"] for one in self.experience.retriever().find(profile)["matches"]]
        again = self.experience.retriever().find(dict(profile, seen=[ranked[0]]))
        self.assertEqual([one["id"] for one in again["matches"]], ranked)

    def test_a_way_of_judging_is_found_by_its_own_words(self) -> None:
        from personalized_agent.agent.work.tools import compact_experience
        self._stage("built the booking page")
        costs = self._nature("Checks what a failed payment costs the guest",
                             "asks what happens when the payment provider errors")
        self._nature("Likes short headings")
        top = self.store.save_pattern({"layer": "pattern", "statement": "Watches money",
                                       "members": [costs.id], "support": 1,
                                       "nature_kind": "work"})
        self.store.save_pattern({"layer": "principle", "statement": "Judges by risk",
                                 "members": [top["id"]], "support": 5,
                                 "nature_kind": "work"})
        said = compact_experience(self.experience.retriever().find(
            {"kind": "evaluation", "text": "payment provider errors booking"}))
        natures = [one["statement"] for one in said["rules"]["natures"]]
        self.assertIn("Checks what a failed payment costs the guest", natures)
        self.assertNotIn("Likes short headings", natures)
        self.assertEqual([one["statement"] for one in said["rules"]["principles"]],
                         ["Judges by risk"])

    def test_a_principle_comes_only_when_the_step_bears_on_it(self) -> None:
        """The most-shown principles came with every question, whatever it
        was about; now none comes to a step nothing of it bears on."""
        from personalized_agent.agent.work.tools import compact_experience
        nature = self._nature("Tries it by hand")
        pattern = self.store.save_pattern({"layer": "pattern", "statement": "Tries things",
                                           "members": [nature.id], "support": 1,
                                           "nature_kind": "work"})
        self.store.save_pattern({"layer": "principle", "statement": "Judges by use",
                                 "members": [pattern["id"]], "support": 9,
                                 "nature_kind": "work"})
        said = compact_experience(self.experience.retriever().find(
            {"kind": "evaluation", "text": "astronomy telescope"}))
        self.assertEqual([one["statement"] for one in said["rules"].get("principles", [])], [])


class FoundByWordsAloneTests(_Base):
    """The user's decision (2026-10-18): stages are found by their words,
    never by an embedding model - measured on a real record, meaning found
    nothing the words had not, for a second and a half a search."""

    _answer = TheWorkIsATreeOfCardsTests._answer
    _built = TheWorkIsATreeOfCardsTests._built

    def test_a_step_sharing_no_word_finds_nothing(self) -> None:
        self._built()
        retriever = self.experience.retriever()
        self.assertFalse(hasattr(retriever, "embedder"))
        self.assertEqual(retriever.find({"text": "make the table grow when clicked"})["matches"],
                         [], "no word in common")
        found = retriever.find({"text": "built the expanding rows"})["matches"]
        self.assertTrue(any(one.startswith("words:") for one in found[0]["why"]))

"""THE TO-DO LIST AND WORK ASSIGNED IN GROUPS (the user, 2026-11-03): grouped
by a model whenever an item comes or changes; the user renames, moves, puts
in Others, makes a new group, adds an empty one, deletes one - each change
kept, the newest 100, and told to the next grouping first. Others is last,
for what fits no group well; an emptied group stays until it is deleted or
the next grouping drops it. Everything here is made up."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401

from personalized_agent.agent.feedback import list_groups as lg
from personalized_agent.agent.feedback.list_groups import ListGroups
from personalized_agent.data_manage.storage.database import Database


class _Model:
    """The model, as the tests play it: what it was asked, and its groups."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def invoke(self, request, role="primary"):
        self.asked.append((request, role))
        answer = self.answer(json.loads(request.messages[0]["content"])) \
            if callable(self.answer) else self.answer
        return SimpleNamespace(text=json.dumps(answer))


ITEMS = [{"id": "t1", "text": "Review paper 12 for the workshop"},
         {"id": "t2", "text": "Review paper 31 for the workshop"},
         {"id": "t3", "text": "Water the tomatoes"}]


class Base(unittest.TestCase):

    def setUp(self) -> None:
        # A grouping runs on a thread of its own, with its own connection to
        # the database: the folder goes as best it can.
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self._dir.name) / "agent.db")
        self.items = {"todo": list(ITEMS), "assigned": []}
        self.model = _Model({"groups": [{"name": "Reviews", "items": ["t1", "t2"]},
                                        {"name": "Garden", "items": ["t3"]}]})
        self.allowed = True
        self.groups = ListGroups(self.db, lambda: self.model,
                                 {"todo": lambda: self.items["todo"],
                                  "assigned": lambda: self.items["assigned"]},
                                 allowed=lambda: self.allowed)

    def tearDown(self) -> None:
        for timer in list(self.groups._timers.values()):
            timer.cancel()
        self.db.close()
        self._dir.cleanup()


class GroupingTests(Base):

    def test_grouped_by_the_model_each_item_once(self) -> None:
        self.groups.group("todo")
        view = self.groups.view("todo", self.items["todo"])
        self.assertEqual(view["groups"], [{"name": "Reviews", "items": ["t1", "t2"]},
                                          {"name": "Garden", "items": ["t3"]}, {"name": "Others", "items": [], "others": True}])
        self.assertEqual(view["new"], [])
        request, role = self.model.asked[0]
        self.assertEqual((request.task, role), ("list_grouping", "heartbeat"))
        self.assertIn("THE USER'S OWN CHANGES COME FIRST", request.system)
        self.assertIn('the group named exactly "Others"', request.system)

    def test_what_the_model_leaves_out_or_makes_up_is_held_by_code(self) -> None:
        self.model.answer = {"groups": [{"name": "Reviews", "items": ["t1", "t1", "x9"]}]}
        self.groups.group("todo")
        view = self.groups.view("todo", self.items["todo"])
        self.assertEqual(view["groups"], [{"name": "Reviews", "items": ["t1"]},
                                          {"name": "Others", "items": ["t2", "t3"], "others": True}])

    def test_others_is_last_and_an_emptied_group_is_dropped_by_the_next_grouping(self) -> None:
        self.model.answer = {"groups": [{"name": "Others", "items": ["t3"]},
                                        {"name": "Reviews", "items": ["t1", "t2"]},
                                        {"name": "Nothing", "items": []}]}
        self.groups.group("todo")
        self.assertEqual([one["name"] for one in self.groups.view("todo", self.items["todo"])["groups"]],
                         ["Reviews", "Others"])

    def test_a_new_item_is_shown_new_and_grouped_at_once(self) -> None:
        self.groups.group("todo")
        self.items["todo"].append({"id": "t4", "text": "Review paper 40 for the workshop"})
        self.model.answer = {"groups": [{"name": "Reviews", "items": ["t1", "t2", "t4"]},
                                        {"name": "Garden", "items": ["t3"]}]}
        lg.WAIT_SECONDS, waited = 0.05, lg.WAIT_SECONDS
        try:
            view = self.groups.view("todo", self.items["todo"])
            self.assertEqual((view["new"], view["grouping"]), (["t4"], True))
            for _ in range(100):
                time.sleep(0.05)
                if not self.groups.view("todo", self.items["todo"])["grouping"]:
                    break
        finally:
            lg.WAIT_SECONDS = waited
        self.assertEqual(self.groups.view("todo", self.items["todo"])["groups"][0]["items"],
                         ["t1", "t2", "t4"])
        self.assertEqual(len(self.model.asked), 2)

    def test_a_grouping_ending_as_the_list_is_looked_at_is_not_asked_for_again(self) -> None:
        """The look compared what it saw before it had the lock: a grouping that ended in
        between was taken for one still to do, and the model was asked once more for nothing."""
        self.groups.group("todo")
        self.items["todo"].append({"id": "t4", "text": "Review paper 40 for the workshop"})
        with self.groups._lock:                  # as a grouping holds it to keep what it found
            looking = threading.Thread(target=self.groups.notice,
                                       args=("todo", self.items["todo"]))
            looking.start()
            time.sleep(0.3)                      # it has come as far as the lock
            kept = self.groups._kept("todo")
            self.groups._keep("todo", kept["groups"], kept["loose"],
                              {str(one["id"]): lg._words(one) for one in self.items["todo"]})
        looking.join(5)
        self.assertFalse(looking.is_alive())
        self.assertNotIn("todo", self.groups._timers, "nothing is left to group")

    def test_the_grouping_there_is_kept_only_the_new_and_edited_placed(self) -> None:
        # The user, 2026-11-03: keep the groups as they were unless really
        # necessary; place only what is new or edited.
        self.groups.group("todo")
        first = json.loads(self.model.asked[0][0].messages[0]["content"])
        self.assertNotIn("changed", first["items"][0], "the first grouping places all")
        self.items["todo"] = [dict(ITEMS[0]), dict(ITEMS[1], text="Review paper 31 by Friday"),
                              dict(ITEMS[2]), {"id": "t4", "text": "Book the tea house"}]
        self.groups.group("todo")
        request = self.model.asked[-1][0]
        handed = json.loads(request.messages[0]["content"])
        self.assertEqual({one["id"]: one.get("changed", "") for one in handed["items"]},
                         {"t1": "", "t2": "edited", "t3": "", "t4": "new"})
        self.assertIn("KEEP THE GROUPING THERE IS", request.system)
        self.assertIn('Only the items marked "new" or "edited" are placed', request.system)

    def test_edited_while_it_was_grouped_it_is_grouped_again(self) -> None:
        # The user, 2026-11-03: an edit made during a grouping is grouped
        # once that grouping is done - whether or not the list was read.
        self.groups.group("todo")

        def answer(handed):
            if len(self.model.asked) == 1:       # the first: an edit comes meanwhile
                self.items["todo"] = [*self.items["todo"], {"id": "t5", "text": "Order seeds"}]
            return {"groups": [{"name": "Reviews", "items": ["t1", "t2"]},
                               {"name": "Garden", "items": [one["id"] for one in handed["items"]
                                                            if one["id"] not in ("t1", "t2")]}]}
        self.model.answer, self.model.asked = answer, []
        self.items["todo"] = [*self.items["todo"], {"id": "t4", "text": "Water the beans"}]
        lg.WAIT_SECONDS, waited = 0.05, lg.WAIT_SECONDS
        try:
            self.groups.notice("todo", self.items["todo"])
            for _ in range(200):
                time.sleep(0.05)
                if len(self.model.asked) >= 2 and not self.groups._timers \
                        and not self.groups._running.get("todo"):
                    break
        finally:
            lg.WAIT_SECONDS = waited
        self.assertEqual(len(self.model.asked), 2, "grouped again for the edit made meanwhile")
        self.assertEqual(self.groups.view("todo", self.items["todo"])["groups"][1]["items"],
                         ["t3", "t4", "t5"])

    def test_a_move_made_while_it_was_grouped_is_not_written_over(self) -> None:
        self.groups.group("todo")
        self.items["todo"] = [*self.items["todo"], {"id": "t4", "text": "Water the beans"}]

        def answer(_handed):
            if len(self.model.asked) == 2:       # the user moves t1 meanwhile
                self.groups.move("todo", "t1", "Garden")
            return {"groups": [{"name": "Reviews", "items": ["t1", "t2"]},
                               {"name": "Garden", "items": ["t3", "t4"]}]}
        self.model.answer = answer
        said = self.groups.group("todo")
        self.assertTrue(said.get("again"))
        view = self.groups.view("todo", self.items["todo"])
        self.assertEqual(view["groups"][1], {"name": "Garden", "items": ["t3", "t1"]})
        self.assertIn("t4", view["new"], "the new one is grouped again")

    def test_nothing_changed_nothing_asked(self) -> None:
        self.groups.group("todo")
        self.groups.view("todo", self.items["todo"])
        self.assertEqual(self.groups._timers, {})

    def test_task_organisation_off_no_call(self) -> None:
        self.allowed = False
        view = self.groups.view("todo", self.items["todo"])
        self.assertEqual((view["new"], view["grouping"]), (["t1", "t2", "t3"], False))


class TheUsersChangesTests(Base):

    def setUp(self) -> None:
        super().setUp()
        self.groups.group("todo")

    def test_renamed_moved_put_in_others_and_a_new_group(self) -> None:
        self.groups.rename("todo", "Reviews", "Workshop reviews")
        self.groups.move("todo", "t2", "Due this week")
        self.groups.move("todo", "t3", "")
        view = self.groups.view("todo", self.items["todo"])
        # Garden emptied stays - the user deletes it, or the next grouping drops it.
        self.assertEqual(view["groups"], [{"name": "Workshop reviews", "items": ["t1"]},
                                          {"name": "Garden", "items": []},
                                          {"name": "Due this week", "items": ["t2"]},
                                          {"name": "Others", "items": ["t3"], "others": True}])
        self.assertEqual([one["said"] for one in self.groups.changes("todo")], [
            'Renamed the group "Reviews" to "Workshop reviews".',
            'Made a new group "Due this week" and put "Review paper 31 for the workshop" in it '
            '(it was in "Workshop reviews").',
            'Took "Water the tomatoes" out of the group "Garden" and put it in "Others" - it fits '
            'none of the groups.'])

    def test_a_group_added_with_nothing_in_it_and_one_deleted(self) -> None:
        view = self.groups.add_group("todo", "Someday")
        self.assertEqual([(one["name"], one["items"]) for one in view["groups"]],
                         [("Reviews", ["t1", "t2"]), ("Garden", ["t3"]), ("Someday", []), ("Others", [])])
        with self.assertRaises(ValueError):
            self.groups.add_group("todo", "Garden")
        with self.assertRaises(ValueError):
            self.groups.add_group("todo", "Others")
        view = self.groups.delete_group("todo", "Garden")
        self.assertEqual([(one["name"], one["items"]) for one in view["groups"]],
                         [("Reviews", ["t1", "t2"]), ("Someday", []), ("Others", ["t3"])])
        with self.assertRaises(ValueError):
            self.groups.delete_group("todo", "Others")
        with self.assertRaises(ValueError):
            self.groups.rename("todo", "Others", "Misc")
        self.assertEqual([one["said"] for one in self.groups.changes("todo")], [
            'Made a new group "Someday", with nothing in it yet.',
            'Deleted the group "Garden" - its 1 item(s) put in "Others".'])
        # The next grouping is told of the empty group, Others last.
        self.groups.group("todo")
        handed = json.loads(self.model.asked[-1][0].messages[0]["content"])
        self.assertEqual([one["name"] for one in handed["the_groups_now"]],
                         ["Reviews", "Someday", "Others"])

    def test_the_next_grouping_is_told_them_first_the_newest_last(self) -> None:
        self.groups.rename("todo", "Reviews", "Workshop reviews")
        self.groups.move("todo", "t3", "")
        self.groups.group("todo")
        handed = json.loads(self.model.asked[-1][0].messages[0]["content"])
        told = handed["the_users_changes_newest_last"]
        self.assertTrue(told[0].endswith('Renamed the group "Reviews" to "Workshop reviews".'))
        self.assertIn("out of the group", told[1])
        self.assertEqual(handed["the_groups_now"][-1], {"name": "Others", "items": ["t3"]})

    def test_the_newest_hundred_kept_older_ones_deleted(self) -> None:
        for n in range(lg.KEEP_CHANGES + 5):
            self.groups.rename("todo", self.groups.view("todo", self.items["todo"])
                               ["groups"][0]["name"], f"Reviews {n}")
        kept = self.groups.changes("todo")
        self.assertEqual(len(kept), lg.KEEP_CHANGES)
        self.assertTrue(kept[-1]["said"].endswith(f'to "Reviews {lg.KEEP_CHANGES + 4}".'))
        self.assertTrue(kept[0]["said"].endswith('to "Reviews 5".'), "the oldest five gone")
        self.assertEqual(self.db.query_one("SELECT COUNT(*) AS n FROM list_group_changes")["n"],
                         lg.KEEP_CHANGES)

    def test_renamed_to_a_group_there_is_the_two_are_one(self) -> None:
        self.groups.rename("todo", "Garden", "Reviews")
        self.assertEqual(self.groups.view("todo", self.items["todo"])["groups"],
                         [{"name": "Reviews", "items": ["t1", "t2", "t3"]}, {"name": "Others", "items": [], "others": True}])

    def test_each_list_on_its_own(self) -> None:
        self.groups.rename("todo", "Reviews", "Workshop reviews")
        self.assertEqual(self.groups.changes("assigned"), [])
        with self.assertRaises(ValueError):
            self.groups.rename("assigned", "Reviews", "Anything")


if __name__ == "__main__":
    unittest.main()

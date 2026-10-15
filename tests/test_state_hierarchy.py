"""A tree rather than a list of groups.

A flat grouping has to choose an altitude before it has seen anything -
which is what the similarity threshold was doing, quietly, for every group
at once. A fortnight on one paper is a handful of sittings on the scripts,
a few on the figures and two on the letter: one thing at one altitude and
three at another, and a list has to pick.
"""

import json
import unittest

from personalized_agent.heartbeat.states.hierarchy import (
    FARTHEST, cluster, cosine, cut, order, tree)


def apart(*pairs):
    """A distance map from (a, b, distance) triples."""
    return {(min(a, b), max(a, b)): d for a, b, d in pairs}


class JoiningTheNearestPairFirstTests(unittest.TestCase):

    #: Two tight clumps of three, one weak link between them.
    TWO_CLUMPS = apart((0, 1, 0.10), (0, 2, 0.12), (1, 2, 0.11),
                       (3, 4, 0.15), (3, 5, 0.14), (4, 5, 0.16),
                       (2, 3, 0.80))

    def test_nothing_to_join(self) -> None:
        self.assertEqual(cluster(0, {}), [])
        self.assertEqual(cluster(1, {}), [])

    def test_two_states_make_one_merge(self) -> None:
        merges = cluster(2, apart((0, 1, 0.3)))
        self.assertEqual(len(merges), 1)
        self.assertAlmostEqual(merges[0].height, 0.3)
        self.assertEqual(merges[0].size, 2)

    def test_everything_ends_up_in_one_tree(self) -> None:
        """Even the parts with nothing between them. A state that joins
        nothing is a finding about that state; a state left out of the
        tree is a state the picture does not mention."""
        merges = cluster(6, self.TWO_CLUMPS)
        self.assertEqual(len(merges), 5)
        self.assertEqual(merges[-1].size, 6)

    def test_the_closest_pairs_are_joined_first(self) -> None:
        """And come back in that order.

        The chain algorithm joins a pair the moment it finds one that any
        order of merging would have joined, which is what makes it fast
        and means the joins arrive in chain order. Nothing downstream
        expects that: a dendrogram drawn in that order references a node
        it has not placed yet and drops the branch without saying so.
        """
        merges = cluster(6, self.TWO_CLUMPS)
        heights = [merge.height for merge in merges]
        self.assertEqual(heights, sorted(heights),
                         "a later merge was closer than an earlier one")

    def test_and_every_join_names_children_that_already_exist(self) -> None:
        merges = cluster(6, self.TWO_CLUMPS)
        for index, merge in enumerate(merges):
            for child in (merge.left, merge.right):
                self.assertLess(child, 6 + index,
                                "a join names a node made after it")

    def test_a_merge_is_a_node_the_next_merge_can_use(self) -> None:
        """Node numbers below the leaf count are states; from there up
        they are the merges themselves, which is the shape everything that
        draws a dendrogram expects."""
        merges = cluster(6, self.TWO_CLUMPS)
        used = {merge.left for merge in merges} | {merge.right for merge in merges}
        self.assertTrue(any(node >= 6 for node in used))

    def test_the_same_states_give_the_same_tree(self) -> None:
        """A run that cannot be compared with the one before it cannot be
        read for what changed."""
        first = [(m.left, m.right, m.height) for m in cluster(6, self.TWO_CLUMPS)]
        again = [(m.left, m.right, m.height) for m in cluster(6, self.TWO_CLUMPS)]
        self.assertEqual(first, again)

    def test_unrelated_states_join_at_the_top(self) -> None:
        merges = cluster(4, apart((0, 1, 0.1), (2, 3, 0.1)))
        self.assertAlmostEqual(merges[-1].height, FARTHEST)

    def test_average_linkage_not_single(self) -> None:
        """Single linkage strings unrelated things together through one
        near-duplicate: a and b close, b and c far, joined at the distance
        of the closest pair. Average says what the two halves are actually
        like."""
        merges = cluster(3, apart((0, 1, 0.1), (1, 2, 0.9), (0, 2, 0.9)))
        self.assertGreater(merges[-1].height, 0.5)


class WhereTheLeavesGoTests(unittest.TestCase):
    """A dendrogram drawn in arrival order crosses its own lines
    everywhere; drawn in the order the merges imply, it does not cross at
    all."""

    def test_every_leaf_appears_once(self) -> None:
        merges = cluster(6, JoiningTheNearestPairFirstTests.TWO_CLUMPS)
        self.assertEqual(sorted(order(6, merges)), list(range(6)))

    def test_and_the_ones_that_merged_first_are_side_by_side(self) -> None:
        merges = cluster(6, JoiningTheNearestPairFirstTests.TWO_CLUMPS)
        leaves = order(6, merges)
        first = {0, 1, 2}
        places = sorted(leaves.index(leaf) for leaf in first)
        self.assertEqual(places, list(range(places[0], places[0] + 3)),
                         "one clump was drawn with the other through it")


class CuttingTheTreeTests(unittest.TestCase):
    """The flat grouping is one cut of the tree. The tree is every cut."""

    def setUp(self) -> None:
        self.merges = cluster(6, JoiningTheNearestPairFirstTests.TWO_CLUMPS)

    def test_a_cut_below_everything_leaves_singletons(self) -> None:
        self.assertEqual(len(cut(6, self.merges, 0.01)), 6)

    def test_a_cut_above_everything_leaves_one_group(self) -> None:
        self.assertEqual(cut(6, self.merges, 1.01), [[0, 1, 2, 3, 4, 5]])

    def test_a_cut_between_them_finds_the_two_clumps(self) -> None:
        self.assertEqual(cut(6, self.merges, 0.5), [[0, 1, 2], [3, 4, 5]])

    def test_the_biggest_group_comes_first(self) -> None:
        merges = cluster(5, apart((0, 1, 0.1), (1, 2, 0.1), (0, 2, 0.1),
                                  (3, 4, 0.1)))
        groups = cut(5, merges, 0.3)
        self.assertEqual(len(groups[0]), 3)


class HowFarOutOfLineAJoinIsTests(unittest.TestCase):
    """A join at the height everything beneath it happened at is one thing
    still being assembled. A join far above them is two things being put
    together, which is where a layer ends."""

    def _root(self, count, distance, **kw):
        from personalized_agent.heartbeat.states.hierarchy import layers

        merges = cluster(count, distance)
        return layers(count, merges, [f"s{i}" for i in range(count)],
                      **{"worth_opening": 2, **kw})[0]

    def test_a_join_far_above_what_it_joins_stands_out(self) -> None:
        root = self._root(4, apart((0, 1, 0.05), (2, 3, 0.05)))
        self.assertGreater(root["stands_out"], 1.15)
        self.assertEqual(len(root["children"]), 2)

    def test_a_steady_assembly_does_not(self) -> None:
        steady = apart((0, 1, 0.10), (0, 2, 0.11), (1, 2, 0.11),
                       (0, 3, 0.12), (1, 3, 0.12), (2, 3, 0.12))
        root = self._root(4, steady)
        self.assertEqual(root["children"], [])

    def test_and_a_difference_of_nothing_is_never_a_boundary(self) -> None:
        """The score is a ratio, so it says "out of line" for differences
        that are nothing at all: two clumps joined at 0.13 whose own
        joins were at 0.10 and 0.11 score 1.34, on a gap of two
        hundredths. The floor is what stops that from becoming a layer -
        and it is a fraction of the tallest join, because the tree sets
        the scale."""
        near = apart((0, 1, 0.10), (0, 2, 0.10), (1, 2, 0.10),
                     (3, 4, 0.11), (3, 5, 0.11), (4, 5, 0.11),
                     *[(a, b, 0.13) for a in (0, 1, 2) for b in (3, 4, 5)])
        split = self._root(6, near, barely_taller=0.04)
        self.assertEqual(len(split["children"]), 2,
                         "without the floor, two hundredths is a boundary")
        held = self._root(6, near, barely_taller=0.4)
        self.assertEqual(held["children"], [])

    def test_a_layer_that_is_one_chain_is_judged_against_that_chain(self) -> None:
        """Which is why the population is the layer rather than a fixed
        number of levels: two levels of a chain is two joins at one
        height, with no spread to divide by."""
        wide = {(pair * 2, pair * 2 + 1): 0.05 for pair in range(6)}
        root = self._root(12, wide, enough_children=8)
        self.assertGreater(root["stands_out"], 1.15)
        self.assertEqual(len(root["children"]), 6)


class ReadingTheTreeAsLayersTests(unittest.TestCase):
    """Every join is a pair, so the tree as it stands is a hundred nested
    pairs. What a reader wants is a few tasks, each holding a few."""

    #: Three clumps: two near each other, one apart.
    THREE_CLUMPS = {}
    for _a, _b in ((0, 1), (0, 2), (1, 2)):
        THREE_CLUMPS[(_a, _b)] = 0.10
    for _a, _b in ((3, 4), (3, 5), (4, 5)):
        THREE_CLUMPS[(_a, _b)] = 0.12
    for _a, _b in ((6, 7), (6, 8), (7, 8)):
        THREE_CLUMPS[(_a, _b)] = 0.08
    for _a in (0, 1, 2):
        for _b in (3, 4, 5):
            THREE_CLUMPS[(_a, _b)] = 0.45

    def _layers(self, count, distance, **kw):
        from personalized_agent.heartbeat.states.hierarchy import layers

        merges = cluster(count, distance)
        return layers(count, merges, [f"s{i}" for i in range(count)],
                      **{"worth_opening": 3, **kw})

    def test_the_layers_are_a_few_tasks_holding_a_few(self) -> None:
        roots = self._layers(9, self.THREE_CLUMPS)
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["size"], 9)
        self.assertEqual(len(roots[0]["children"]), 2)

    def test_and_open_again_where_there_is_more_inside(self) -> None:
        # Again with room to go deeper than the default two layers: the
        # recursion is what is being tested here, not the cap.
        root = self._layers(9, self.THREE_CLUMPS, deepest=4)[0]
        deeper = [child for child in root["children"] if child["children"]]
        self.assertEqual(len(deeper), 1)
        self.assertEqual([piece["size"] for piece in deeper[0]["children"]],
                         [3, 3])

    def test_a_task_assembled_state_by_state_is_one_layer(self) -> None:
        """The collapse. Five states that came together one at a time are
        five nested pairs in the tree and one task on the page."""
        chain = {}
        for a in range(6):
            for b in range(a + 1, 6):
                chain[(a, b)] = 0.10 + 0.004 * (a + b)
        for a in range(6, 9):
            for b in range(a + 1, 9):
                chain[(a, b)] = 0.09
        root = self._layers(9, chain)[0]
        sizes = sorted(child["size"] for child in root["children"])
        self.assertEqual(sizes, [3, 6])
        for child in root["children"]:
            self.assertEqual(child["children"], [],
                             "a chain of joins became nested pairs")

    def test_a_task_too_small_to_split_in_two_is_not_opened(self) -> None:
        """Splitting three states leaves pieces of one and two, and a
        piece of one is not a task."""
        # Deeper than the default cap of two, so that the SIZE rule is
        # what closes the task rather than the layer count. Both rules
        # close a task and each needs its own test: one says "a piece of
        # this would not be a task", the other says "this is the last
        # layer", and they are different findings.
        root = self._layers(9, self.THREE_CLUMPS, deepest=4)[0]
        closed = [child for child in root["children"] if not child["children"]]
        self.assertTrue(closed)
        self.assertIn("Splitting it would leave a piece",
                      closed[0]["why_closed"])

    def test_and_the_last_layer_says_that_is_why_it_closed(self) -> None:
        """Two layers, and the second one is a list of states."""
        root = self._layers(9, self.THREE_CLUMPS)[0]
        closed = [child for child in root["children"] if not child["children"]]
        self.assertTrue(closed)
        self.assertIn("last of the 2 layers", closed[0]["why_closed"])
        self.assertTrue(closed[0]["last_layer"])

    def test_and_nothing_is_deeper_than_the_cap(self) -> None:
        def depth_of(task, level=1):
            if not task["children"]:
                return level
            return max(depth_of(child, level + 1)
                       for child in task["children"])

        for want in (1, 2, 3):
            with self.subTest(want):
                roots = self._layers(9, self.THREE_CLUMPS, deepest=want)
                self.assertLessEqual(max(depth_of(one) for one in roots), want)

    def test_every_closed_task_says_which_reason_closed_it(self) -> None:
        """Three different findings: too few states to open, nothing under
        it but states, and a join that does not stand out. A task that
        stops without saying which is a task nobody can act on."""
        root = self._layers(9, self.THREE_CLUMPS)[0]
        closed = [child for child in root["children"] if not child["children"]]
        self.assertTrue(closed)
        for task in closed:
            self.assertTrue(task["why_closed"], task["id"])

    def test_a_join_over_an_outlier_scores_far_above_a_steady_one(self) -> None:
        """Which is what makes the cutoff a number worth setting: twenty
        states about one thing with a stranger joined at 0.9 scores near
        6, where four states assembled at 0.10, 0.11, 0.12 scores near 1.
        """
        stranger = {}
        for a in range(20):
            for b in range(a + 1, 20):
                stranger[(a, b)] = 0.10
        for a in range(20):
            stranger[(a, 20)] = 0.90
        odd = self._layers(21, stranger, worth_opening=2)[0]
        steady = self._layers(4, apart(
            (0, 1, 0.10), (0, 2, 0.11), (1, 2, 0.11),
            (0, 3, 0.12), (1, 3, 0.12), (2, 3, 0.12)), worth_opening=2)[0]
        self.assertGreater(odd["stands_out"], 4.0)
        self.assertLess(steady["stands_out"], 2.0)

    def test_a_task_of_too_few_states_is_not_opened(self) -> None:
        root = self._layers(9, self.THREE_CLUMPS, worth_opening=8)[0]
        for child in root["children"]:
            self.assertEqual(child["children"], [])
            self.assertIn("a task is opened at 8", child["why_closed"])

    def test_every_task_carries_the_states_under_it(self) -> None:
        """A task whose states cannot be listed is a label."""
        root = self._layers(9, self.THREE_CLUMPS)[0]
        self.assertEqual(sorted(root["states"]),
                         sorted(f"s{i}" for i in range(9)))

    def test_and_says_how_far_out_of_line_its_own_join_was(self) -> None:
        root = self._layers(9, self.THREE_CLUMPS)[0]
        self.assertGreater(root["stands_out"], 1.15)

    def test_a_layer_of_too_many_keeps_the_largest(self) -> None:
        """Past a point the layer is the list it was meant to replace, so
        the rest join the states that fell outside it."""
        wide = {(pair * 2, pair * 2 + 1): 0.05 for pair in range(6)}
        root = self._layers(12, wide, worth_opening=2, enough_children=4)[0]
        self.assertEqual(len(root["children"]), 4)
        # And nothing is lost: what is not a sub-task is a loose state.
        self.assertEqual(
            sum(child["size"] for child in root["children"])
            + len(root["loose"]), 12)

    def test_a_piece_too_small_to_be_a_task_is_a_loose_state(self) -> None:
        """Not a task of one, and not hidden either: a state that grouped
        with nothing is a finding about that state."""
        d = {}
        for a in range(6):
            for b in range(a + 1, 6):
                d[(a, b)] = 0.10
        for a in range(6, 12):
            for b in range(a + 1, 12):
                d[(a, b)] = 0.12
        for a in (12, 13):
            d[(0, a)] = 0.95
        root = self._layers(14, d, worth_opening=5)[0]
        self.assertEqual(sorted(child["size"] for child in root["children"]),
                         [6, 6])
        self.assertEqual(len(root["loose"]), 2)

    def test_the_same_tree_gives_the_same_layers(self) -> None:
        first = self._layers(9, self.THREE_CLUMPS)
        again = self._layers(9, self.THREE_CLUMPS)
        self.assertEqual(json.dumps(first), json.dumps(again))


class HowAlikeTwoVectorsPointTests(unittest.TestCase):

    def test_the_same_direction_is_one(self) -> None:
        self.assertAlmostEqual(cosine([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]), 1.0)

    def test_at_right_angles_is_nothing(self) -> None:
        self.assertAlmostEqual(cosine([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_opposite_is_not_worse_than_unrelated(self) -> None:
        """Two descriptions of somebody's afternoon cannot be opposite,
        and a negative similarity would make a distance above 1 that the
        tree then has to explain."""
        self.assertEqual(cosine([1.0, 0.0], [-1.0, 0.0]), 0.0)

    def test_an_empty_or_mismatched_vector_is_nothing(self) -> None:
        self.assertEqual(cosine([], [1.0]), 0.0)
        self.assertEqual(cosine([1.0, 2.0], [1.0]), 0.0)


class TheTreeAsThePanelReadsItTests(unittest.TestCase):

    def test_it_carries_the_leaves_in_drawing_order(self) -> None:
        merges = cluster(6, JoiningTheNearestPairFirstTests.TWO_CLUMPS)
        drawn = tree(6, merges, [f"state {i}" for i in range(6)])
        self.assertEqual(drawn["count"], 6)
        self.assertEqual(sorted(drawn["leaves"]), list(range(6)))
        self.assertEqual(len(drawn["merges"]), 5)
        self.assertEqual(len(drawn["labels"]), 6)

    def test_and_says_where_the_tree_starts(self) -> None:
        merges = cluster(6, JoiningTheNearestPairFirstTests.TWO_CLUMPS)
        self.assertEqual(tree(6, merges, [])["roots"], [10])

    def test_a_single_state_is_a_tree_of_one(self) -> None:
        drawn = tree(1, [], ["only"])
        self.assertEqual(drawn["leaves"], [0])
        self.assertEqual(drawn["merges"], [])
        self.assertEqual(drawn["roots"], [0])


if __name__ == "__main__":
    unittest.main()

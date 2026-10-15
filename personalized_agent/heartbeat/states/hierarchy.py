"""Agglomerative clustering, and the tree it leaves behind.

The flat grouping answered "which of these states are one thing" and
stopped there. A user's work is not flat: a fortnight on one paper is a
handful of sittings on the scripts, a few on the figures and two on the
letter, and those three are one thing at one altitude and three at
another. A list of groups has to choose an altitude in advance - which is
what the similarity threshold was doing, quietly, for every group at once.

A tree does not choose. Every state is joined to its nearest neighbour,
then those pairs to theirs, all the way up to one root, and the height of
each join says how alike the two halves were. Cutting the tree at a height
gives the flat grouping back; the tree itself is every cut at once.

Average linkage, on cosine distance. Average rather than single (which
strings unrelated things into chains through one near-duplicate) or
complete (which refuses to join a large group to anything). It is the
usual choice for text and it is the one whose height means something a
reader can state: the average distance between the two halves.

Built with the nearest-neighbour chain algorithm, which is exact and does
n-1 merges in O(n^2) total rather than the O(n^3) of rescanning the matrix
each time. At four hundred states that is the difference between a
hundred and sixty thousand operations and sixty-four million, which is the
difference between a panel that draws and one that hangs.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

#: How far apart two states with nothing in common are.
#:
#: Cosine distance runs 0 (the same direction) to 2 (opposite), but text
#: embeddings never oppose - the far end in practice is "unrelated", which
#: is 1. Kept as the ceiling so that a height can be read as a fraction.
FARTHEST = 1.0


class Merge:
    """One join: two children, and how far apart they were."""

    def __init__(self, left: int, right: int, height: float, size: int) -> None:
        self.left = left
        self.right = right
        self.height = height
        self.size = size

    def to_dict(self) -> Dict[str, Any]:
        return {"left": self.left, "right": self.right,
                "height": round(self.height, 4), "size": self.size}


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """How alike two vectors point, 0 to 1.

    Negative cosines are clamped away rather than kept. Two states that
    point in opposite directions are not *more* different than two with
    nothing in common - "opposite" is not a thing a pair of descriptions
    of somebody's afternoon can be - and a negative similarity would make
    a distance above 1, which the tree then has to explain.
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    size = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if size <= 0:
        return 0.0
    return max(0.0, min(1.0, dot / size))


def cluster(count: int, distance: Dict[Tuple[int, int], float]
            ) -> List[Merge]:
    """Join everything into one tree, nearest pair first.

    `distance` holds the pairs that are closer than the ceiling; any pair
    missing from it is `FARTHEST` apart. That matters at four hundred
    states, where most pairs are unrelated and holding all eighty thousand
    of them costs more than the clustering.

    Returns the merges in the order they happened. Node numbers below
    `count` are the original states; from `count` upwards they are the
    merges themselves, so merge `i` is node `count + i` - the shape
    everything that draws a dendrogram expects.
    """
    if count <= 0:
        return []
    if count == 1:
        return []

    def apart(a: int, b: int) -> float:
        if a == b:
            return 0.0
        return distance.get((a, b) if a < b else (b, a), FARTHEST)

    #: The live clusters, by node number, and how many leaves each holds.
    #: Lance-Williams for average linkage needs only the sizes, which is
    #: why the members themselves are not carried here.
    live: Dict[int, int] = {index: 1 for index in range(count)}
    between: Dict[Tuple[int, int], float] = {}
    for (a, b), value in distance.items():
        between[(a, b) if a < b else (b, a)] = value

    def gap(a: int, b: int) -> float:
        if a == b:
            return 0.0
        return between.get((a, b) if a < b else (b, a), FARTHEST)

    merges: List[Merge] = []
    next_node = count
    chain: List[int] = []
    while len(live) > 1:
        if not chain:
            chain.append(next(iter(live)))
        # Walk to the nearest neighbour, and to its nearest neighbour,
        # until two of them are each other's - which is a pair that any
        # order of merging would have joined, so joining it now is the
        # same answer for less work.
        while True:
            node = chain[-1]
            nearest, best = None, None
            for other in live:
                if other == node:
                    continue
                span = gap(node, other)
                # Ties go to the lower node number, so the same states in
                # the same state give the same tree.
                if best is None or span < best or (span == best
                                                   and other < (nearest or 0)):
                    nearest, best = other, span
            if nearest is None:
                break
            if len(chain) > 1 and nearest == chain[-2]:
                break
            chain.append(nearest)
        if nearest is None:
            break
        left, right = chain.pop(), chain.pop()
        height = gap(left, right)
        size = live[left] + live[right]
        merges.append(Merge(left, right, height, size))
        joined = next_node
        next_node += 1
        # Lance-Williams, average linkage: the new cluster's distance to
        # everything else is the sizes-weighted mean of its two halves'.
        for other in list(live):
            if other in (left, right):
                continue
            mixed = ((live[left] * gap(left, other)
                      + live[right] * gap(right, other)) / size)
            if mixed < FARTHEST:
                between[(joined, other) if joined < other
                        else (other, joined)] = mixed
        for node in (left, right):
            del live[node]
            for other in list(live) + [joined]:
                between.pop((node, other) if node < other else (other, node),
                            None)
        live[joined] = size
    return _in_height_order(count, merges)


def _in_height_order(count: int, merges: List[Merge]) -> List[Merge]:
    """The same tree, with the joins in the order they happened by height.

    The chain algorithm finds a pair that any order of merging would have
    joined and joins it at once, which is what makes it fast - and means
    the joins come out in chain order rather than in height order. The
    tree is the same either way, but nothing downstream expects that: a
    dendrogram drawn in emitted order references a node it has not placed
    yet, and skips the branch without saying so.

    Average linkage is reducible, so the heights hold no inversions and
    sorting cannot invent one. The node numbers are rewritten to match,
    because a merge's number is its place in this list.
    """
    ordered = sorted(range(len(merges)),
                     key=lambda index: (merges[index].height, index))
    renumbered = {count + old: count + new
                  for new, old in enumerate(ordered)}

    def moved(node: int) -> int:
        return renumbered.get(node, node) if node >= count else node

    return [Merge(moved(merges[old].left), moved(merges[old].right),
                  merges[old].height, merges[old].size)
            for old in ordered]


def order(count: int, merges: Sequence[Merge]) -> List[int]:
    """The leaves, left to right, as the tree puts them.

    A dendrogram drawn in the order the states arrived crosses its own
    lines everywhere; drawn in the order the merges imply, it does not
    cross at all. That is the whole reason a dendrogram is readable, and
    it costs one walk of the tree.
    """
    children: Dict[int, Tuple[int, int]] = {}
    for index, merge in enumerate(merges):
        children[count + index] = (merge.left, merge.right)
    roots = _roots(count, merges)
    out: List[int] = []
    for root in roots:
        stack = [root]
        while stack:
            node = stack.pop()
            if node in children:
                left, right = children[node]
                # Right first, so the left child comes off the stack first
                # and the walk reads left to right.
                stack.append(right)
                stack.append(left)
            else:
                out.append(node)
    return out


def _roots(count: int, merges: Sequence[Merge]) -> List[int]:
    """Whatever was never merged into anything else.

    Usually one. More than one when the graph was in pieces - which
    happens when nothing at all connects two halves of the calendar, and
    is a finding rather than a failure.
    """
    joined = set()
    for merge in merges:
        joined.add(merge.left)
        joined.add(merge.right)
    every = set(range(count)) | {count + i for i in range(len(merges))}
    return sorted(every - joined)


def cut(count: int, merges: Sequence[Merge], height: float) -> List[List[int]]:
    """The flat grouping the tree gives at one height.

    Every merge below the height is kept; everything above it is left
    apart. This is what the old flat clustering produced, except that the
    height is now a knob on a picture rather than a threshold decided
    before anything was seen.
    """
    parent: Dict[int, int] = {}

    def root(node: int) -> int:
        while node in parent:
            node = parent[node]
        return node

    members: Dict[int, List[int]] = {index: [index] for index in range(count)}
    for index, merge in enumerate(merges):
        if merge.height > height:
            continue
        joined = count + index
        left, right = root(merge.left), root(merge.right)
        parent[left] = joined
        parent[right] = joined
        members[joined] = members.get(left, []) + members.get(right, [])
    groups = [sorted(members[root(index)]) for index in range(count)]
    seen: List[List[int]] = []
    for group in groups:
        if group not in seen:
            seen.append(group)
    return sorted(seen, key=lambda one: (-len(one), one[0]))


def tree(count: int, merges: Sequence[Merge], labels: Sequence[Any],
         ) -> Dict[str, Any]:
    """The whole thing, as the panel reads it."""
    return {
        "leaves": list(order(count, merges)),
        "merges": [merge.to_dict() for merge in merges],
        "roots": _roots(count, merges),
        "labels": list(labels),
        "count": count,
    }




#: How far out of line a join has to be to count as a boundary.
#:
#: In standard deviations of the joins in the layer it heads. 1.15 is the
#: value scipy's own documentation uses for cutting a tree by
#: inconsistency, and it lands where it should here: a join at 0.45 over a
#: layer that joined at 0.10 scores above 2, where the steady assembly
#: inside one project scores 0.
#:
#: Lower splits more - every slight rise becomes a sub-task - and higher
#: gives one flat list again. It is a setting because the right value
#: depends on how varied the user's work is, and every task on the page
#: says its own score, so the number can be judged rather than guessed.
#: How many layers deep the reading goes, at the most.
#:
#: TWO. A task, and the tasks inside it - and then the states themselves,
#: as a list. Not three: the third level was a tree the reader had to hold
#: in their head to know where they were, and it never said anything the
#: second did not, because by then the pieces are a handful of states each
#: and the honest way to present a handful of states is to present them.
#:
#: This also bounds what the splitting threshold has to be good at, which
#: is the reason it is worth fixing rather than leaving to the data. The
#: threshold is not being asked to find every real division at every scale
#: - only to tell ONE division apart: is this task one thing, or is it a
#: few. A number that has to answer that is a far easier number to choose
#: than one that has to be right about however many levels the tree
#: happens to hold.
DEEPEST_LAYERS = 2

STANDS_OUT = 1.15

#: The smallest height difference that means anything, as a fraction of
#: the tallest join in this tree.
#:
#: It does two jobs, because they are the same judgement. Two joins closer
#: together than this are the same moment of the same assembly, so one
#: collapses into the other; and a join less than this above what it joins
#: is not a boundary however its score reads.
#:
#: A fraction rather than a distance, because the tree sets the scale. On
#: real states the joins climb in a smooth ramp - 0.07, 0.18, 0.25, 0.32,
#: 0.36, 0.37, 0.52, 0.61, 0.74, 0.81 - where a fixed five hundredths is
#: nothing at the top and everything at the bottom: it collapsed nothing,
#: so every layer was a pair and the whole calendar came out as one task.
#: A fifth of the tallest join reads that same tree as the four things it
#: is.
#:
#: The score needs the floor because it is a ratio, and a ratio explodes
#: where the spread underneath is near zero: two clumps joined at 0.13
#: whose own joins were at 0.10 and 0.11 score 1.34 on a gap of two
#: hundredths.
BARELY_TALLER = 0.2

#: How many states a task must hold to be worth opening.
#:
#: Below it the task is shown with its states and not split further: a
#: task of three is three states, and a reader asking what is under it is
#: better served by the three than by a tree of them.
WORTH_OPENING = 5

#: How many sub-tasks one layer may show before the rest are gathered.
#:
#: Eight is about what a person reads as "a few". Above it the layer stops
#: being a summary of what is underneath and becomes the list it was meant
#: to replace.
ENOUGH_CHILDREN = 8


def _joins_under(node: int, children: Dict[int, Tuple[int, int]],
                 heights: Dict[int, float]) -> List[float]:
    """Every join beneath this one, and a 0 for every single state.

    The population a join is judged against. A state on its own came
    together at no distance at all, and counting it says so - without it,
    fifty states with one stranger among them read exactly like fifty
    states.
    """
    out: List[float] = []
    stack = list(children.get(node, ()))
    while stack:
        here = stack.pop()
        out.append(heights.get(here, 0.0))
        stack.extend(children.get(here, ()))
    return out


def layers(count: int, merges: Sequence[Merge], labels: Sequence[Any],
           *, stands_out: float = STANDS_OUT,
           barely_taller: float = BARELY_TALLER,
           worth_opening: int = WORTH_OPENING,
           enough_children: int = ENOUGH_CHILDREN,
           deepest: int = DEEPEST_LAYERS) -> List[Dict[str, Any]]:
    """The binary tree read as a few tasks, each holding a few.

    Every join is a pair, so the tree as it stands is a hundred nested
    pairs. A layer is made by splitting a task where the tree gives most
    - the join with the largest drop beneath it - and splitting again,
    until the layer holds `enough_children` tasks or no split is left
    worth making.

    Splitting where the gaps are biggest, rather than taking every join
    within a band of the top one, is what keeps this readable on a real
    calendar. The band was the first version: at four hundred states the
    joins near the root are dense, a fifth of the tallest join swept up
    three hundred and fifty of them, and the layer came out as seven
    tasks and a pile.

    A PIECE OF FEWER THAN `worth_opening` STATES IS NOT A TASK. It is not
    a task of one, and it is not hidden either: its states are listed on
    the task it sits in, as states that fall outside every sub-task. That
    is a finding about those states - they did not group with anything -
    and it is the honest way to say it.

    A split has to be worth making: the join must be at least
    `barely_taller` of the scale above what it joins, and `stands_out`
    standard deviations above the joins beneath it. The first is the
    floor that stops a rounding difference from becoming a layer; the
    second is the inconsistency criterion, measured against every join in
    the subtree.
    """
    children: Dict[int, Tuple[int, int]] = {
        count + index: (merge.left, merge.right)
        for index, merge in enumerate(merges)}
    heights = {count + index: merge.height for index, merge in enumerate(merges)}
    # The scale is the tree's own: the tallest join is as far apart as
    # anything in this calendar got, and every other distance is read
    # against it.
    tallest = max(heights.values()) if heights else 1.0
    least_gap = barely_taller * tallest
    sizes: Dict[int, int] = {}

    def size_of(node: int) -> int:
        if node not in children:
            return 1
        if node not in sizes:
            left, right = children[node]
            sizes[node] = size_of(left) + size_of(right)
        return sizes[node]

    def leaves_under(node: int) -> List[int]:
        if node not in children:
            return [node]
        out: List[int] = []
        stack = [node]
        while stack:
            here = stack.pop()
            if here in children:
                stack.extend(reversed(children[here]))
            else:
                out.append(here)
        return out

    def stands_out_score(node: int) -> float:
        """How far this join sits above everything beneath it, in standard
        deviations of those joins.

        The inconsistency criterion, measured against the whole subtree
        rather than against the heads of the pieces: the heads are by
        definition the tallest things underneath, so a population of those
        has almost no spread and the score lands near the same value for
        every tree - a number that decides nothing.

        A state on its own counts for the nothing it joined at. Without
        that, twenty states with a stranger among them read exactly like
        twenty states.
        """
        if node not in children:
            return 0.0
        population = [heights[node]] + _joins_under(node, children, heights)
        if len(population) < 3:
            return 0.0
        mean = sum(population) / len(population)
        spread = math.sqrt(sum((value - mean) ** 2 for value in population)
                           / len(population))
        if spread <= 0:
            return 0.0
        return (heights[node] - mean) / spread

    def drop(node: int) -> float:
        """How far this join is above the two things it joins."""
        if node not in children:
            return 0.0
        under = [heights.get(child, 0.0) for child in children[node]]
        return heights[node] - max(under)

    def worth_splitting(node: int) -> bool:
        """Whether this task is several things rather than one."""
        return node in children and stands_out_score(node) >= stands_out

    def unfold(node: int, top: float) -> List[int]:
        """The pieces of a task, passing through the joins that join
        nothing.

        A join within `least_gap` of the top of this layer happened at
        the same distance as the layer itself: it is the tree putting
        already-separate things into some order, which it has to do
        because it only makes pairs. The top of a real tree is a chain of
        those - eleven states joined at 0.81, 0.74, 0.61 where the things
        they join are at 0.3 and below - and passing through them is what
        turns a chain of pairs into one layer of several.

        It stops at anything meaningfully tighter than the layer, which
        is a piece of it, and at single states.

        Measured against the top of the layer rather than against each
        join's own children: a tight clump is itself a chain of joins at
        one height, so a rule about a join and its children cannot tell
        the chain at the top of the tree from the chain inside a clump,
        and descends through both.
        """
        if node not in children or top - heights[node] >= least_gap:
            return [node]
        left, right = children[node]
        return unfold(left, top) + unfold(right, top)

    def task(node: int, name: str, depth: int) -> Dict[str, Any]:
        """One task, and - unless this is the last layer - the tasks in it."""
        members = leaves_under(node)
        entry: Dict[str, Any] = {
            "id": name,
            "depth": depth,
            "size": len(members),
            "height": round(heights.get(node, 0.0), 4),
            "stands_out": round(stands_out_score(node), 2),
            "states": [labels[leaf] for leaf in members
                       if 0 <= leaf < len(labels)],
            "loose": [],
            "children": [],
        }
        if depth + 1 >= max(1, int(deepest)):
            # The last layer, and what is in it is NOT listed out. Every
            # layer of this tree is summarised work; the states inside a
            # task are what it was written from, not a level of their own.
            entry["why_closed"] = (
                f"This is the last of the {max(1, int(deepest))} layers, so "
                f"the {len(members)} states in it are not divided again. A "
                f"further level would be a tree the reader has to hold in "
                f"their head, and by here the pieces are a handful of "
                f"states each.")
            entry["last_layer"] = True
            return entry
        if len(members) < worth_opening * 2:
            # It cannot be split into two tasks, because one of them would
            # be smaller than a task is allowed to be.
            entry["why_closed"] = (
                f"{len(members)} states. Splitting it would leave a piece "
                f"of fewer than {worth_opening}, which is not a task.")
            return entry
        if not worth_splitting(node):
            entry["why_closed"] = (
                f"Its own join is {entry['stands_out']} standard deviations "
                f"above the joins beneath it, under the {stands_out:g} it "
                f"would take to be a boundary - so the states in it came "
                f"together at much the same distance all the way down. One "
                f"thing being assembled rather than several joined.")
            return entry
        pieces = unfold(node, heights.get(node, 0.0))
        kept = sorted((piece for piece in pieces
                       if size_of(piece) >= worth_opening),
                      key=lambda piece: -size_of(piece))
        spare = [piece for piece in pieces if size_of(piece) < worth_opening]
        if len(kept) < 2:
            entry["why_closed"] = (
                f"Nothing inside it divides into two pieces of "
                f"{worth_opening} states or more.")
            return entry
        if len(kept) > enough_children:
            # The largest are the layer; the rest join the states that
            # fell outside it, because a layer of forty is the list this
            # was meant to replace.
            spare.extend(kept[enough_children:])
            kept = kept[:enough_children]
        entry["children"] = [task(piece, f"{name}.{index + 1}", depth + 1)
                             for index, piece in enumerate(kept)]
        # Everything that did not land in a sub-task, said as that rather
        # than as a task of one. These are states that group with nothing:
        # a finding about them, not a gap in the picture.
        entry["loose"] = [labels[leaf] for piece in spare
                          for leaf in leaves_under(piece)
                          if 0 <= leaf < len(labels)]
        return entry

    return [task(root, f"task_{index + 1}", 0)
            for index, root in enumerate(_roots(count, merges))]


__all__ = ["DEEPEST_LAYERS",
           "Merge", "cluster", "cosine", "cut", "layers", "order", "tree",
           "FARTHEST", "STANDS_OUT", "BARELY_TALLER", "WORTH_OPENING",
           "ENOUGH_CHILDREN"]

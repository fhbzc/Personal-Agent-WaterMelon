"""Graph based context retrieval (sections 41-44).

The whole graph is never loaded.  Retrieval starts from candidate entry nodes,
inspects the *summaries* of neighbouring edges, selects the next node, and
stops as soon as the budget is exhausted or nothing useful remains.  When the
local neighbourhood is not useful, a global escape step jumps elsewhere in the
graph (section 43) instead of getting stuck.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from ...common.events.models import EdgeView, KnowledgeNode
from ..graph.store import GraphStore

log = logging.getLogger(__name__)

_WORD = re.compile(r"[\w']+", re.UNICODE)


def tokenize(text: str) -> List[str]:
    return [w.lower() for w in _WORD.findall(text or "") if len(w) > 1]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


#: Everything in the graph is one of three kinds.
#:
#: What the user is *doing* is no longer here: it changes by the hour, it has
#: a beginning and an end, and it belongs on the calendar as a state (section
#: 34.1). What is left in the graph is what keeps: who the user is, and the
#: world they work in.
USER_NATURE = "user_nature"      # habits, character, how they work
OTHER = "other"                  # everything else
#: Section 52.4: what the user said about something the agent decided.
#:
#: Kept apart from the other two because it is the only memory that records a
#: judgement of the agent rather than a fact about the user or their world. An
#: agent that cannot reach its own past misjudgements repeats them, and mixed
#: in with everything else this is reached only by accident.
USER_FEEDBACK = "user_feedback"  # the user's verdict on what the agent did

CATEGORIES = (USER_NATURE, OTHER, USER_FEEDBACK)


def category_of(node_type: str) -> str:
    """Which of the two a node belongs to. Everything is one of them."""
    kind = str(node_type or "").strip().lower()
    if kind in (USER_NATURE, USER_FEEDBACK):
        return kind
    return OTHER

#: How often a step ignores the weights and goes somewhere at random.
DEFAULT_EXPLORATION = 0.15

#: How many steps one path takes, unless the user says otherwise. Twelve paths
#: of this length is the most one gathering can hold.
DEFAULT_MAX_PATH_LENGTH = 4

#: Added to every weight when sampling, so that a connection pushed down to
#: nothing becomes unlikely rather than impossible. A single unhelpful answer
#: must not sever a link permanently.
WEIGHT_FLOOR = 0.05


@dataclass
class RetrievalBudget:
    #: The user's ceiling on how much one answer may be built from. The agent
    #: asks for what it needs and receives the smaller of the two.
    max_nodes: int = 12
    max_depth: int = 4
    max_iterations: int = 8
    token_budget: int = 6000
    time_budget_seconds: float = 30.0

    @staticmethod
    def from_config(config: Any) -> "RetrievalBudget":
        return RetrievalBudget(
            max_nodes=int(config.get("retrieval_max_nodes", 12)),
            max_depth=int(config.get("retrieval_max_depth", 4)),
            max_iterations=int(config.get("retrieval_max_iterations", 8)),
            token_budget=int(config.get("retrieval_token_budget", 6000)),
            time_budget_seconds=float(config.get("retrieval_time_budget_seconds", 30)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_nodes": self.max_nodes,
            "max_depth": self.max_depth,
            "max_iterations": self.max_iterations,
            "token_budget": self.token_budget,
            "time_budget_seconds": self.time_budget_seconds,
        }


@dataclass
class RetrievalStep:
    node_id: str
    title: str
    depth: int
    reason: str
    via_edge: Optional[str] = None
    relationship: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "depth": self.depth,
            "reason": self.reason,
            "via_edge": self.via_edge,
            "relationship": self.relationship,
        }


@dataclass
class RetrievalResult:
    query: str
    nodes: List[KnowledgeNode] = field(default_factory=list)
    path: List[RetrievalStep] = field(default_factory=list)
    tokens_used: int = 0
    iterations: int = 0
    stopped_because: str = ""
    global_escapes: int = 0
    #: The connections walked, as information in their own right (section 42).
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    #: How long each path was asked to be, and how long it was allowed.
    path_length_requested: Optional[int] = None
    path_length: int = 0
    #: Section 34.1: what was true at the time, established before the graph
    #: was walked. These are context in their own right, not routing.
    states: List[Any] = field(default_factory=list)

    def context_text(self, max_chars_per_node: int = 1200) -> str:
        blocks = []
        if self.states:
            # Section 34.1: what is true right now comes first, and each line
            # says where it came from - the agent's inference and the user's
            # own instruction carry very different authority.
            blocks.append("### What is true right now\n"
                          + "\n".join(state.as_context() for state in self.states))
        if not self.nodes:
            if blocks:
                return "\n\n".join(blocks)
            return "(no relevant knowledge graph context was found)"
        for node in self.nodes:
            body = node.content or node.summary
            if len(body) > max_chars_per_node:
                body = body[:max_chars_per_node] + " ..."
            blocks.append(
                f"### {node.title}  [{node.type}, id={node.id}]\n"
                f"summary: {node.summary}\n{body}".strip()
            )
        if self.relationships:
            # Section 42: the connections are information, not routing. That
            # two things are related, and how, is often the part worth
            # knowing, and it is lost if only the nodes are handed over.
            lines = [
                f"- {r['from_title']} --{r['relationship'] or 'relates to'}--> "
                f"{r['to_title']}"
                + (f": {r['summary']}" if r.get("summary") else "")
                for r in self.relationships
            ]
            blocks.append("### How these are connected\n" + "\n".join(lines))
        return "\n\n".join(blocks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "node_ids": [n.id for n in self.nodes],
            "titles": [n.title for n in self.nodes],
            "path": [s.to_dict() for s in self.path],
            "tokens_used": self.tokens_used,
            "iterations": self.iterations,
            "stopped_because": self.stopped_because,
            "global_escapes": self.global_escapes,
            "relationships": self.relationships,
            "path_length_requested": self.path_length_requested,
            "path_length": self.path_length,
            "states": [state.to_dict() for state in self.states],
        }


class GraphRetriever:
    """Gathering context by walking the graph (sections 42, 42.1, as amended).

    The walk is probabilistic rather than searched. Where it goes next is
    drawn from the weights of the connections leading out of where it is, and
    some of the time it goes somewhere at random instead.

    This is a different idea from looking things up. A search returns what
    matches the words of the question, which means the same few memories
    answer everything and the rest are never seen again - and the weights,
    which are the only record of what has actually proved related, do no work
    at all. Sampling by weight lets what has been useful before be likely
    without being certain, and the random share is what lets the agent come
    across something it had stopped expecting.
    """

    def __init__(self, graph: GraphStore, config: Any = None,
                 states: Any = None) -> None:
        self.graph = graph
        self.config = config
        #: The state calendar, where there is one. Retrieval works without it,
        #: with less to say about what is currently going on.
        self.states = states
        self._random = random.Random()

    # -- the two dials ------------------------------------------------------
    def exploration_rate(self) -> float:
        """How often a step ignores the weights and goes somewhere at random."""
        if self.config is None:
            return DEFAULT_EXPLORATION
        try:
            value = float(self.config.get("retrieval_exploration_rate",
                                          DEFAULT_EXPLORATION))
        except (TypeError, ValueError):
            return DEFAULT_EXPLORATION
        return max(0.0, min(1.0, value))

    def path_length_for(self, asked: Optional[int]) -> int:
        """How many steps each path takes, as asked and as allowed.

        The agent judges how far it needs to go for the question in front of
        it, which it is better placed to know than any fixed number is. The
        ceiling is the user's, and it applies to the length of a path rather
        than to the size of the gathering, because the number of paths is
        fixed: capping the length caps the total.
        """
        ceiling = DEFAULT_MAX_PATH_LENGTH
        if self.config is not None:
            try:
                ceiling = max(1, int(self.config.get("retrieval_max_path_length",
                                                     DEFAULT_MAX_PATH_LENGTH)))
            except (TypeError, ValueError):
                ceiling = DEFAULT_MAX_PATH_LENGTH
        if asked is None:
            return ceiling
        try:
            wanted = int(asked)
        except (TypeError, ValueError):
            return ceiling
        return max(1, min(ceiling, wanted))

    # -- where to go next ---------------------------------------------------
    def sample_edge(self, views: Sequence[EdgeView]) -> Optional[EdgeView]:
        """One outgoing connection, drawn from the weights.

        A weight is how related two things have proved to be, so it is used as
        a likelihood rather than a ranking: the strongest link is the most
        likely next step and not the inevitable one.
        """
        usable = [view for view in views if view is not None]
        if not usable:
            return None
        if self._random.random() < self.exploration_rate():
            return self._random.choice(usable)
        # A weight can be zero or negative once it has been pushed down; such
        # a connection should be unlikely, not impossible, or a single bad
        # answer would sever it permanently.
        weights = [max(float(view.weight), 0.0) + WEIGHT_FLOOR for view in usable]
        total = sum(weights)
        if total <= 0:
            return self._random.choice(usable)
        threshold = self._random.random() * total
        running = 0.0
        for view, weight in zip(usable, weights):
            running += weight
            if running >= threshold:
                return view
        return usable[-1]

    # -- the four routes ----------------------------------------------------
    def routes(self) -> List[Dict[str, Any]]:
        """The four kinds of walk a gathering is made of.

        Three stay inside one kind of memory, so each is followed on its own
        terms: who the user is leads to more of who they are, the world leads
        to more of the world, and a judgement the user made leads to the other
        judgements they have made. A walk that crosses freely wanders off
        whatever it started on after a single step.

        The fourth crosses deliberately and in order - nature, world,
        feedback, nature, world, feedback - which is the shape of an
        explanation: this is the person, this is what it concerns, this is
        what they thought of it last time.
        """
        return [
            {"name": "nature", "start": USER_NATURE, "cycle": (USER_NATURE,)},
            {"name": "world", "start": OTHER, "cycle": (OTHER,)},
            {"name": "feedback", "start": USER_FEEDBACK,
             "cycle": (USER_FEEDBACK,)},
            {"name": "across", "start": USER_NATURE,
             "cycle": (USER_NATURE, OTHER, USER_FEEDBACK)},
        ]

    def _entry(self, category: str, query: str,
               visited: Set[str]) -> Optional[KnowledgeNode]:
        """Where a route begins: what the question mentions, else anywhere.

        Only the entry is chosen this way. Once a walk has begun, where it
        goes is drawn from the weights, because those are the record of what
        has proved related. But beginning somewhere the question actually
        mentions is strictly better than beginning nowhere in particular, and
        it costs one query per route.

        Among the nodes that do share words, the start is still drawn rather
        than taken: the best match is the most likely beginning, not the
        certain one, so the same question does not always start in the same
        place.
        """
        candidates = [(score, node)
                      for score, node in self.graph.search_in_category(query, category)
                      if node.id not in visited]
        if candidates:
            total = sum(score for score, _ in candidates)
            if total > 0:
                threshold = self._random.random() * total
                running = 0.0
                for score, node in candidates:
                    running += score
                    if running >= threshold:
                        return node
            return candidates[0][1]
        # Nothing of this kind shares a word with the question - ordinary when
        # the question is in one language and the notes another, or when it is
        # about something new. The route still runs.
        return self.graph.random_node(category=category, excluding_ids=list(visited))

    def _walk(self, route: Dict[str, Any], budget: RetrievalBudget,
              length: int, visited: Set[str], result: RetrievalResult,
              started: float) -> None:
        """One path: a fixed number of steps, or fewer if memory runs out.

        Every path is the same length, so no one starting point can dominate
        a gathering by happening to sit in a dense part of the graph. The
        length is the agent's to choose and the user's to cap.
        """
        cycle: Sequence[Optional[str]] = route["cycle"]
        node = route.get("node")
        if node is None:
            node = (self._entry(route["start"], result.query, visited)
                    if route.get("by") == "relevance"
                    else self.graph.random_node(category=route["start"],
                                                excluding_ids=list(visited)))
        if node is None:
            return
        depth = 0
        taken = 0
        reason = f"{route['name']}: {route.get('how', 'started')}"
        edge_id = relationship = None

        while node is not None and taken < length:
            if result.iterations >= budget.max_iterations:
                return
            if result.tokens_used >= budget.token_budget:
                return
            if time.monotonic() - started > budget.time_budget_seconds:
                return
            if node.id in visited:
                return
            visited.add(node.id)
            taken += 1
            result.iterations += 1
            result.nodes.append(node)
            result.tokens_used += estimate_tokens(
                f"{node.title}{node.summary}{node.content}")
            result.path.append(RetrievalStep(node.id, node.title, depth, reason,
                                             edge_id, relationship))

            # Where this path may go next: the next kind in its cycle, or
            # anywhere at all where the cycle names no kind.
            wants = cycle[(depth + 1) % len(cycle)]
            views = [v for v in self.graph.outgoing(node.id)
                     if v.target_node_id not in visited
                     and (wants is None or category_of(v.target_type) == wants)]
            chosen = self.sample_edge(views)
            if chosen is None:
                # No connection of the kind this path needs. Rather than end
                # short, begin again somewhere else of that kind - a path is a
                # pattern of kinds, not one unbroken chain. A path that names
                # no kind has nowhere particular to restart, so it stops.
                if wants is None:
                    return
                node = self.graph.random_node(category=wants,
                                              excluding_ids=list(visited))
                if node is None:
                    return
                result.global_escapes += 1
                depth += 1
                reason = (f"{route['name']}: no connection into {wants}; "
                          f"started again there")
                edge_id = relationship = None
                continue
            target = self.graph.get_node(chosen.target_node_id)
            if target is None:
                return
            result.relationships.append({
                "edge_id": chosen.edge_id,
                "from_id": node.id,
                "from_title": node.title,
                "to_id": target.id,
                "to_title": target.title,
                "relationship": chosen.relationship_type,
                "summary": chosen.relationship_summary,
                "weight": float(chosen.weight),
                "route": route["name"],
            })
            depth += 1
            reason = (f"{route['name']}: followed "
                      f"{chosen.relationship_type or 'a connection'} "
                      f"from {node.title}")
            edge_id = chosen.edge_id
            relationship = chosen.relationship_summary or chosen.relationship_type
            node = target

    def paths(self) -> List[Dict[str, Any]]:
        """Every path walked for one gathering: four kinds, three starts each -
        twelve walks in all.

        Each kind of walk is started three times over. One start is chosen for
        relevance - the node in that kind of memory that best matches the
        question - and two are drawn at random.

        Both are needed and they do different jobs. The relevant start answers
        the question that was asked. The random ones are the only way anything
        the question did not mention can ever be reached, and a memory that
        can never be reached may as well not be kept. Three starts of the same
        length also means one dense corner of the graph cannot crowd out the
        rest of a gathering.
        """
        out: List[Dict[str, Any]] = []
        for route in self.routes():
            out.append(dict(route, name=f"{route['name']}/relevant",
                            by="relevance", how="started near the question"))
            for index in (1, 2):
                out.append(dict(route, name=f"{route['name']}/random{index}",
                                by="random", how="started at random"))
        return out

    # -- traversal ----------------------------------------------------------
    def retrieve(
        self,
        query: str,
        budget: Optional[RetrievalBudget] = None,
        starting_node_ids: Optional[List[str]] = None,
        path_length: Optional[int] = None,
    ) -> RetrievalResult:
        budget = budget or RetrievalBudget()
        started = time.monotonic()
        result = RetrievalResult(query=query)
        # Section 34.1, as amended: establish what is true before walking what
        # is known. A state spanning the week is as true today as it was on
        # Monday, and every answer given today needs it.
        if self.states is not None:
            try:
                result.states = self.states.covering()
            except Exception:          # pragma: no cover - context is an aid
                log.debug("could not read the state calendar", exc_info=True)
        length = self.path_length_for(path_length)
        result.path_length = length
        result.path_length_requested = path_length
        visited: Set[str] = set()

        if starting_node_ids:
            # Named entry points: the walk begins there rather than being
            # confined to them, and may cross between kinds freely - naming a
            # starting point is asking about that thing, not asking to stay
            # inside one kind of memory.
            for identifier in starting_node_ids:
                node = self.graph.get_node(identifier)
                if node is None:
                    continue
                self._walk({"name": "named", "cycle": (None,), "node": node,
                            "how": "started at the node asked for"},
                           budget, length, visited, result, started)
            result.stopped_because = ("gathered from the nodes asked for"
                                      if result.nodes else "those nodes are not in the graph")
            return result

        for path in self.paths():
            self._walk(path, budget, length, visited, result, started)

        if not result.nodes:
            result.stopped_because = "the knowledge graph is empty"
        elif not result.nodes:
            result.stopped_because = "the knowledge graph is empty"
        else:
            result.stopped_because = f"walked {len(self.paths())} paths of up to {length}"
        return result

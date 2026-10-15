# deprecated

Code that is kept but no longer runs. Nothing in the application imports
anything here; the migration script does, and that is the only thing that
should.

## The knowledge graph (`graph/`, `retrieval/`, `compression.py`)

Superseded on 2026-09-16 by `personalized_agent/data_manage/library/` — five
folders of files with an index built by the data heartbeat.

The graph stored what the agent knew as nodes joined by weighted edges, and
gathered context by twelve probabilistic walks, each step drawn from the
weights on the edges leading out of wherever it was.

The argument for walking rather than searching was that a search returns what
matches the words of the question, so the same few memories answer everything
and the rest are never seen again. That argument was right about the failure
and wrong about the remedy, because it assumed the weights meant something.
Measured on a live installation two and a half weeks in:

| | |
|---|---|
| edges | 3,688 |
| ever judged against an answer | 53 (1.4%) |
| recorded retrievals to learn from | 17, total |
| edges at 0.80 / 0.85 / 0.90 | 61% |

The weights were a language model's one-shot guess, made when the edge was
written, and almost never revised — so the walk was sampling from noise. The
variety it produced was not insight, it was chance, and the design's central
claim could not be checked by the user or by anyone else, because the whole
structure lived in a database nobody could read.

What the graph was genuinely good at is kept:

- Its three retrieval categories became three of the five **folders**, so a
  gathering still takes from each rather than letting the wordiest subject win
- Its **keywords** (section 51.1) became what the index is built from
- The thing it was trying and failing to learn numerically — what is worth
  looking at, and how to look for it — became the **agent-lesson** folder,
  where it is written in words that can be read and corrected

`knowledge_graph_plan_apply.txt` in the repository root records the design
that was considered instead: Bayesian co-relevance with a forgetting factor,
applied at keyword level. It was not adopted. Its own analysis is the reason —
every estimator in it needs many observations per pair, and this system had
seventeen observations in total.

## Reading this code

It is left exactly as it ran, including the comments explaining decisions that
are no longer in force. Do not treat those as current: `data_manage/library/`
is what the application does now, and the design doc's sections 39.1 onward,
as amended, are what it is supposed to do.

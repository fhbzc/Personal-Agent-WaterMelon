"""What a state means, as a vector.

The grouping used to be word overlap: how many words two states have in
common, over how many they have. On this calendar that measures the wrong
thing. One model writes every state, in a stable vocabulary, about a
handful of projects - so two states picked at random already share a
sixth of their words, and the words they share are the project's rather
than the work's. Overlap can see that `replication` appears twice; it
cannot see that "rebuilding the SWR replication package" and "rebuilding
the replication package for the SWR paper" are one thing said twice.

An embedding can: those two come out at 0.87 to each other and 0.06 to an
evening of YouTube, where overlap gave 0.74 and 0.10.

Local, English, and small. `all-MiniLM-L6-v2` is ninety megabytes and 384
dimensions, runs on the processor in about four milliseconds a state, and
never leaves the machine - which matters more here than the accuracy of a
larger model would, because the material is a record of everything the
user does. The states are written in English for this reason (section
34.1, as amended), which is what lets the model be the small one.

Optional, like everything else in this application that needs more than
the standard library: with no model on disk the state heartbeat falls
back to comparing words, says so, and carries on.
"""

from __future__ import annotations

import array
import base64
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import (Any, Callable, Dict, List, Optional,
                    Sequence, Tuple)

log = logging.getLogger(__name__)

#: The flag the packaged application is started with to be the worker.
EMBED_FLAG = "--embedding-worker"

#: The model, unless the settings name another.
#:
#: English-only and 384-dimensional on purpose. A multilingual model of the
#: same quality is five times the size, and the states are written in
#: English precisely so that this one is enough.
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

#: How many states are embedded in one pass through the model.
BATCH = 32

#: How much of a state is read. Titles and details are a sentence and a
#: paragraph; this is well above both and stops a pathological one from
#: costing a second.
MAX_TOKENS = 256

#: Below this the two states are treated as unrelated and the pair is not
#: kept. Not a threshold on the grouping - the tree joins everything
#: eventually - but a bound on how much of an all-pairs matrix is carried
#: around, which at four hundred states is eighty thousand numbers.
FLOOR = 0.05


class Embedder:
    """The model, in a child process held while something uses it, with what
    it has already read kept.

    LOADED WHEN IT IS NEEDED, GONE WHEN IT IS NOT. Torch and transformers
    are about 450 MB once imported, and an import cannot be taken back - so
    the model is never loaded in the agent's own process. It runs in a
    child (`embed_worker.py`) that is started when a state round, a
    project's processing, a pattern build or a look-up of past work begins,
    and ended when the last of them finishes: `with embedder.loaded(): ...`.
    A call made outside any of them starts it for that call alone.

    Loading takes about sixteen seconds - the import of `transformers` is
    most of it - and is paid once per round, never on the thread the user
    is waiting on.

    A vector is kept against the text it came from, not against the state:
    a state whose title is edited is a different sentence and needs
    reading again, and one that is merely seen again does not.
    """

    #: How long the child may take to load, and to answer one batch.
    LOAD_SECONDS = 300
    ANSWER_SECONDS = 120

    def __init__(self, config: Any = None, db: Any = None) -> None:
        self.config = config
        self.db = db
        self._lock = threading.Lock()
        # ONE READING AT A TIME. The round and the patterns built beside it
        # both read through this model, and it is one model.
        self._using = threading.RLock()
        self._child: Any = None
        self._holders = 0
        self._failed = ""
        self._loaded_ms = 0

    # -- what it is ---------------------------------------------------------
    @property
    def name(self) -> str:
        if self.config is None:
            return DEFAULT_MODEL
        return str(self.config.get("state_embedding_model", DEFAULT_MODEL)
                   or DEFAULT_MODEL)

    @property
    def wanted(self) -> bool:
        if self.config is None:
            return True
        return bool(self.config.get("state_embedding_enabled", True))

    def status(self) -> Dict[str, Any]:
        return {"wanted": self.wanted, "model": self.name,
                "loaded": self._running(),
                "load_ms": self._loaded_ms,
                "unavailable": self._failed}

    # -- held while in use ----------------------------------------------------
    @contextmanager
    def loaded(self):
        """Hold the model for a piece of work: loaded at its first use inside,
        and ended when the last piece holding it is done."""
        with self._lock:
            self._holders += 1
        try:
            yield self
        finally:
            with self._lock:
                self._holders = max(0, self._holders - 1)
                last = self._holders == 0
            if last:
                self.release()

    def release(self) -> None:
        """End the child, if nothing holds it - its memory goes with it."""
        with self._using:
            with self._lock:
                if self._holders:
                    return
                child, self._child = self._child, None
            if child is not None:
                _end(child)
                log.info("embedding model %s released", self.name)

    def _running(self) -> bool:
        child = self._child
        return child is not None and child.poll() is None

    def _start(self) -> bool:
        """The child, started and loaded - or why it could not be."""
        if self._running():
            return True
        if self._failed:
            return False
        began = time.perf_counter()
        try:
            from ...common.children import popen

            child = popen(_worker_command(self.name),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True,
                          encoding="utf-8", bufsize=1,
                          env=dict(os.environ, USE_TF="0",
                                   TRANSFORMERS_VERBOSITY="error"),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                          if sys.platform.startswith("win") else 0)
        except Exception as exc:     # pragma: no cover - no interpreter to run
            self._failed = f"{type(exc).__name__}: {str(exc)[:200]}"
            log.info("no embedding model (%s); comparing words instead",
                     self._failed)
            return False
        said = _read_line(child, self.LOAD_SECONDS)
        if not said.get("ready"):
            _end(child)
            self._failed = str(said.get("error") or "the model did not load")
            log.info("no embedding model (%s); comparing words instead",
                     self._failed)
            return False
        self._child = child
        self._loaded_ms = int((time.perf_counter() - began) * 1000)
        log.info("embedding model %s loaded in %d ms, in process %s",
                 self.name, self._loaded_ms, child.pid)
        return True

    def available(self) -> bool:
        """Whether there is a model to read with.

        Held, by starting it - so a model that will not load is found out
        before the work that needs it. Not held, by whether the libraries
        are installed, without loading anything: starting it only to let
        it go would cost the sixteen seconds twice, once here and again for
        the reading that follows.
        """
        if not self.wanted or self._failed:
            return False
        if not self._holders:
            return _installed()
        with self._using:
            return self._start()

    # -- reading ------------------------------------------------------------
    def embed(self, texts: Sequence[str],
              while_ok: Optional[Callable[[], bool]] = None
              ) -> List[List[float]]:
        with self._using:
            try:
                return self._embed(texts, while_ok)
            finally:
                if not self._holders:
                    self.release()

    def _embed(self, texts: Sequence[str],
               while_ok: Optional[Callable[[], bool]] = None
               ) -> List[List[float]]:
        """Read a batch of texts. Empty when there is no model.

        `while_ok` is asked between batches and the reading stops when it
        says no. A thousand states is thirty-odd batches and the better
        part of a minute, and an application being closed should not wait
        for the end of it: the caller gets what was read so far and can
        decide, which for a round that is being abandoned means throwing
        it away. Asked BETWEEN batches rather than during one, because a
        batch is milliseconds and a half-finished tensor is nobody's
        friend.
        """
        if not texts or not self.wanted or not self._start():
            return []
        out: List[List[float]] = []
        for start in range(0, len(texts), BATCH):
            if while_ok is not None and not while_ok():
                log.debug("stopped reading after %s of %s", len(out), len(texts))
                break
            batch = [str(text or "") for text in texts[start:start + BATCH]]
            child = self._child
            try:
                child.stdin.write(json.dumps({"texts": batch}) + "\n")
                child.stdin.flush()
            except (OSError, ValueError, AttributeError):
                log.info("the embedding process went away; reading stopped")
                self._child = None
                break
            said = _read_line(child, self.ANSWER_SECONDS)
            if "vectors" not in said:
                log.info("the embedding process did not answer (%s)",
                         said.get("error", "no answer"))
                _end(child)
                self._child = None
                break
            out.extend(_vectors_of(said))
        return out

    def for_states(self, states: Sequence[Any],
                   while_ok: Optional[Callable[[], bool]] = None
                   ) -> Dict[str, List[float]]:
        """A vector per state, reading only the ones not already read.

        The cache is the reason this is affordable on every round: a
        thousand states are read once, and an hour later only the handful
        written since are new.
        """
        return self.read_states(states, while_ok) or {}

    def read_states(self, states: Sequence[Any],
                    while_ok: Optional[Callable[[], bool]] = None
                    ) -> Optional[Dict[str, List[float]]]:
        """`for_states`, or None where `available()` would have said no.

        THE CACHE IS LOOKED IN BEFORE THE MODEL IS STARTED. This asked
        `available()` first, which while the model is held means starting
        it - sixteen to twenty-one seconds and three hundred megabytes for
        the child - and only then found that every vector it wanted was
        already in the database. A round in which nothing new was written
        paid the whole load to read nothing. Now the child is started only
        when a vector is missing; the vectors that come back are the same
        rows either way. Where nothing is missing and the libraries are
        not even installed, `available()` is still asked, so a machine
        without them is told so in the same words as before.
        """
        if not self.wanted or self._failed:
            return None
        with self.loaded():
            wanted: Dict[str, str] = {}
            for state in states:
                state_id = str(getattr(state, "id", "") or "")
                if state_id:
                    wanted[state_id] = text_of(state)
            kept = self._kept(wanted)
            missing = [(state_id, text) for state_id, text in wanted.items()
                       if state_id not in kept]
            if not missing and (self._running() or _installed()):
                return kept
            if not self.available():
                return None
            if missing:
                vectors = self.embed([text for _id, text in missing],
                                     while_ok)
                for (state_id, _text), vector in zip(missing, vectors):
                    kept[state_id] = vector
                self._keep([(state_id, text, vector) for (state_id, text),
                            vector in zip(missing, vectors)])
            return kept

    def pairwise(self, order: Sequence[str], vectors: Dict[str, List[float]],
                 floor: float = FLOOR) -> List[Tuple[int, int, float]]:
        return _pairwise(order, vectors, floor)

    # -- what has already been read -----------------------------------------
    #: How many ids one look-up names - under SQLite's oldest limit of
    #: 999 parameters, with the model's name beside them.
    LOOKUP = 900

    def _kept(self, wanted: Dict[str, str]) -> Dict[str, List[float]]:
        """The vectors already read for these states, and no others.

        BY ID, NOT THE WHOLE TABLE. The table also holds what the patterns
        read - twenty thousand nature records beside sixteen hundred
        states, thirty-five megabytes - and every round read all of it to
        keep the sixteen hundred: 221 ms, against 70 ms by asking for the
        ids. The rows come back in the order the whole table gave them
        (by rowid), so the dict is the same dict, key order included.
        """
        if self.db is None or not wanted:
            return {}
        ids = list(wanted)
        rows: List[Dict[str, Any]] = []
        try:
            for start in range(0, len(ids), self.LOOKUP):
                part = ids[start:start + self.LOOKUP]
                rows.extend(self.db.query(
                    "SELECT rowid AS seat, state_id, fingerprint, vector "
                    "FROM state_vectors WHERE model = ? AND state_id IN ("
                    + ",".join("?" * len(part)) + ")",
                    (self.name, *part)))
        except Exception:            # pragma: no cover - a table not yet there
            log.debug("could not read the kept vectors", exc_info=True)
            return {}
        rows.sort(key=lambda row: row["seat"])
        kept: Dict[str, List[float]] = {}
        for row in rows:
            state_id = row["state_id"]
            text = wanted.get(state_id)
            if text is None or row["fingerprint"] != _fingerprint(text):
                continue
            kept[state_id] = list(array.array("f", row["vector"]))
        return kept

    def _keep(self, read: Sequence[Tuple[str, str, Sequence[float]]]) -> None:
        """What was just read, kept - in one transaction, not one each: a
        first round reads a thousand states, and a commit per row was a
        thousand trips to the disk for a cache."""
        if self.db is None:
            return
        at = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = [(state_id, self.name, _fingerprint(text), len(vector),
                 array.array("f", vector).tobytes(), at)
                for state_id, text, vector in read if vector]
        if not rows:
            return
        try:
            self.db.executemany(
                "INSERT INTO state_vectors (state_id, model, fingerprint, "
                "dims, vector, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(state_id) DO UPDATE SET model = excluded.model, "
                "fingerprint = excluded.fingerprint, dims = excluded.dims, "
                "vector = excluded.vector, created_at = excluded.created_at",
                rows)
        except Exception:            # pragma: no cover - a cache, not a record
            log.debug("could not keep %d vectors", len(rows), exc_info=True)


def text_of(state: Any) -> str:
    """What is read: the title and the detail, and nothing else.

    Not the evidence or the sources. Those are paths and URLs - a language
    model reading `C:/Research/replication_SWR/scripts/census_api/fetch.py`
    is reading punctuation - and they are matched by the words in them or
    not at all.
    """
    title = str(getattr(state, "title", "") or "")
    detail = str(getattr(state, "detail", "") or "")
    return f"{title} {detail}".strip()


def _fingerprint(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:32]


def _pairwise(order: Sequence[str], vectors: Dict[str, List[float]],
              floor: float = FLOOR) -> List[Tuple[int, int, float]]:
    """Every pair that is at all alike, as (i, j, cosine).

    With numpy, in this process: four hundred states is eighty thousand
    pairs of three hundred and eighty-four numbers - a matrix multiply of a
    few milliseconds, or half a minute of arithmetic in a loop. The upper
    triangle and the floor are taken at once, so only the pairs that
    survive are ever turned into Python: at a thousand states, walking the
    whole result in Python took ninety-three seconds of a
    hundred-and-eighteen-second rebuild.
    """
    rows = [vectors.get(state_id) for state_id in order]
    present = [index for index, row in enumerate(rows) if row]
    if len(present) < 2:
        return []
    try:
        import numpy
    except ImportError:              # pragma: no cover - numpy is a dependency
        return _pairwise_slowly(rows, present, floor)
    matrix = numpy.asarray([rows[index] for index in present],
                           dtype=numpy.float32)
    scores = numpy.clip(matrix @ matrix.T, 0.0, 1.0)
    left, right = numpy.triu_indices(len(present), k=1)
    values = scores[left, right]
    keeping = values >= floor
    left, right, kept = (left[keeping].tolist(), right[keeping].tolist(),
                         values[keeping].tolist())
    return [(present[left[index]], present[right[index]], kept[index])
            for index in range(len(kept))]


def _installed() -> bool:
    """Whether the libraries the worker loads are here - looked up, not
    imported."""
    import importlib.util
    try:
        return all(importlib.util.find_spec(name) is not None
                   for name in ("torch", "transformers"))
    except Exception:                # pragma: no cover - a broken install
        return False


def _worker_command(model: str) -> List[str]:
    """How the child is started: the worker script by its path, or - in the
    packaged application, where there is no script to run - the
    application itself with the flag that makes it the worker."""
    if getattr(sys, "frozen", False):
        return [sys.executable, EMBED_FLAG, model]
    return [sys.executable, os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "embed_worker.py"), model]


def _read_line(child: Any, seconds: float) -> Dict[str, Any]:
    """One answer from the child, or {} if none comes in time."""
    got: Dict[str, Any] = {}

    def read() -> None:
        try:
            line = child.stdout.readline()
            got.update(json.loads(line) if line.strip() else {})
        except Exception:            # pragma: no cover - a broken pipe
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(seconds)
    return got


def _vectors_of(said: Dict[str, Any]) -> List[List[float]]:
    count, dims = int(said.get("n") or 0), int(said.get("dims") or 0)
    if not count or not dims:
        return []
    flat = array.array("f")
    flat.frombytes(base64.b64decode(said["vectors"]))
    return [list(flat[row * dims:(row + 1) * dims]) for row in range(count)]


def _end(child: Any) -> None:
    """Let the child finish - the end of its input ends it - or end it."""
    try:
        if child.stdin:
            child.stdin.close()
        child.wait(timeout=10)
    except Exception:
        from ...common.children import end_tree
        end_tree(child)


def _pairwise_slowly(rows: Sequence[Optional[List[float]]],
                     present: Sequence[int], floor: float
                     ) -> List[Tuple[int, int, float]]:
    """The same thing without a tensor library, for a small enough graph."""
    out: List[Tuple[int, int, float]] = []
    for a_index in range(len(present)):
        for b_index in range(a_index + 1, len(present)):
            a, b = present[a_index], present[b_index]
            left, right = rows[a] or [], rows[b] or []
            score = max(0.0, min(1.0, sum(x * y for x, y in zip(left, right))))
            if score >= floor:
                out.append((a, b, score))
    return out


__all__ = ["Embedder", "DEFAULT_MODEL", "EMBED_FLAG", "FLOOR", "text_of"]

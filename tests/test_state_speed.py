"""What a round no longer pays for, and proof it gets the same answers.

Each of these was time or money spent on an answer the round already had:
the embedding model started to read vectors that were all in the database,
the whole vector table read to keep a sixteenth of it, the plan of the
lot asked of the model on every visit with the same text, and the status
calls made one after another while there was room for two. The tests ask
both halves - that the cost is gone, and that what comes out is what came
out before.
"""

import array
import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import TemporaryAgent  # noqa: E402

from personalized_agent.common.events.models import utcnow  # noqa: E402
from personalized_agent.data_manage.storage.database import Database  # noqa: E402
from personalized_agent.heartbeat.states import embedding  # noqa: E402
from personalized_agent.heartbeat.states.embedding import Embedder  # noqa: E402


class _State:
    def __init__(self, state_id, title, detail=""):
        self.id, self.title, self.detail = state_id, title, detail


class _Counted(Embedder):
    """The embedder with its child replaced: starting it is counted, and
    reading gives a vector made from the text."""

    def __init__(self, db):
        super().__init__(config=None, db=db)
        self.starts = 0
        self.read = []

    def _start(self):
        self.starts += 1
        return True

    def _running(self):
        return False

    def _embed(self, texts, while_ok=None):
        self.read.extend(texts)
        return [[float(len(text)), 1.0, 0.5] for text in texts]


class TheModelIsStartedOnlyForWhatIsMissingTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa-speed-")
        self.db = Database(Path(self.dir) / "agent.db")
        self.states = [_State(f"state_{n}", f"Title {n}", f"detail {n}")
                       for n in range(5)]
        self._installed = embedding._installed
        embedding._installed = lambda: True

    def tearDown(self):
        embedding._installed = self._installed
        self.db.close()

    def test_a_round_with_nothing_new_does_not_start_it(self):
        first = _Counted(self.db)
        with first.loaded():
            read = first.read_states(self.states)
        self.assertEqual(first.starts, 1)
        again = _Counted(self.db)
        with again.loaded():
            kept = again.read_states(self.states)
        self.assertEqual(again.starts, 0)
        self.assertEqual(again.read, [])
        self.assertEqual(list(kept.items()), list(read.items()))

    def test_one_new_state_starts_it_once_and_reads_only_that_one(self):
        first = _Counted(self.db)
        first.read_states(self.states)
        more = self.states + [_State("state_new", "Something new")]
        again = _Counted(self.db)
        with again.loaded():
            got = again.read_states(more)
        self.assertEqual(again.starts, 1)
        self.assertEqual(again.read, ["Something new"])
        self.assertEqual(set(got), {one.id for one in more})

    def test_without_the_libraries_it_still_says_there_is_no_model(self):
        _Counted(self.db).read_states(self.states)
        embedding._installed = lambda: False

        class Broken(_Counted):
            def _start(self):
                self.starts += 1
                self._failed = "no torch"
                return False

        broken = Broken(self.db)
        with broken.loaded():
            self.assertIsNone(broken.read_states(self.states))
        self.assertEqual(broken.for_states(self.states), {})

    def test_the_kept_vectors_are_the_ones_a_whole_table_read_gave(self):
        reader = _Counted(self.db)
        reader.read_states(self.states)
        # Rows that are not states - what the patterns keep - beside them.
        for n in range(30):
            self.db.execute(
                "INSERT INTO state_vectors (state_id, model, fingerprint, "
                "dims, vector, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (f"rec_{n}", reader.name, "x", 3,
                 array.array("f", [0.0, 0.0, 1.0]).tobytes(), "now"))
        wanted = {one.id: embedding.text_of(one)
                  for one in reversed(self.states)}
        whole = {}
        for row in self.db.query("SELECT state_id, fingerprint, vector FROM "
                                 "state_vectors WHERE model = ?",
                                 (reader.name,)):
            text = wanted.get(row["state_id"])
            if text is not None and row["fingerprint"] == embedding._fingerprint(text):
                whole[row["state_id"]] = list(array.array("f", row["vector"]))
        old_lookup = Embedder.LOOKUP
        Embedder.LOOKUP = 2          # several look-ups, to test the joining
        try:
            kept = reader._kept(wanted)
        finally:
            Embedder.LOOKUP = old_lookup
        self.assertEqual(list(kept.items()), list(whole.items()))

    def test_what_is_read_is_kept_in_one_go(self):
        writes = []
        real = self.db.executemany

        def counted(sql, rows):
            rows = list(rows)
            writes.append(len(rows))
            return real(sql, rows)

        self.db.executemany = counted
        _Counted(self.db).read_states(self.states)
        self.assertEqual(writes, [5])
        self.assertEqual(self.db.query_one(
            "SELECT COUNT(*) AS n FROM state_vectors")["n"], 5)


class _Models:
    """Answers from the text alone, so the same question gets the same
    answer whichever order the calls are made in - and counts them."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.sent = []
        self.going = 0
        self.most = 0
        self._lock = threading.Lock()

    def invoke(self, request, role=None):
        import hashlib

        content = request.messages[-1]["content"]
        meta = dict(request.metadata or {})
        with self._lock:
            self.sent.append((meta.get("kind") or
                              ("plan" if "pieces" in meta else "summary"),
                              content))
            self.going += 1
            self.most = max(self.most, self.going)
        time.sleep(self.delay)
        with self._lock:
            self.going -= 1
        # By the lines, not their order: which sample is shown first is
        # not fixed from one folder to the next, and is not what is tested.
        key = hashlib.sha256(_lines(content).encode("utf-8")).hexdigest()[:8]
        if meta.get("kind") == "status":
            body = {"changed": True, "status": f"status {key}",
                    "why": f"why {key}", "urgency": int(key[0], 16) % 7 + 1,
                    "importance": int(key[1], 16) % 7 + 1}
        elif "pieces" in meta:
            body = {"depends": [[2, 1]], "finished": [1], "why": f"plan {key}"}
        elif meta.get("kind") == "projects":
            body = {}
        else:
            body = {"title": f"Work {key}", "summary": f"Summary {key}",
                    "concrete": True, "status": f"first {key}",
                    "urgency": int(key[0], 16) % 7 + 1,
                    "importance": int(key[1], 16) % 7 + 1}

        class Reply:
            text = json.dumps(body)
            usage = None

        return Reply()

    def resolve(self, *a, **k):
        return type("R", (), {"to_dict": lambda s: {}})()

    def check_availability(self, *a, **k):
        return type("A", (), {"available": True, "to_dict": lambda s: {},
                              "state": None})()

    def refused_since(self, *a, **k):
        return ""


def _lines(text):
    """A prompt as a set of lines, less where each sample came in the list."""
    import re

    return "\n".join(sorted(re.sub(r"^--- \d+ \[", "--- [", line)
                            for line in str(text).splitlines()))


class _Room:
    def __init__(self, allowed):
        self.n = allowed

    def allowed(self, running):
        return self.n, "as the test says"


GROUPS = {
    "repl": ["They are rebuilding the SWR replication package.",
             "They are rebuilding the replication package for the SWR paper.",
             "They are working through the replication scripts again.",
             "They are fixing the replication code for the SWR paper.",
             "They are still rebuilding that replication package.",
             "They are checking the replication figures against the paper."],
    "video": ["They are watching videos on YouTube.",
              "They are watching another YouTube video.",
              "They are on YouTube again this evening.",
              "They are watching a long video on YouTube.",
              "They are back on YouTube after dinner.",
              "They are watching YouTube videos late."],
    "thesis": ["They are drafting the thesis introduction chapter.",
               "They are revising the thesis introduction chapter.",
               "They are editing the thesis introduction chapter text.",
               "They are restructuring the thesis introduction chapter.",
               "They are polishing the thesis introduction chapter.",
               "They are rereading the thesis introduction chapter."],
}


class TwoRoundsOfTheSameFolderTests(unittest.TestCase):
    """The same folder, the same answers, one at a time or two at once."""

    def _rounds(self, allowed, delay=0.0):
        from personalized_agent.agent.states.store import State
        from personalized_agent.heartbeat.states.tasks import Kept

        agent = TemporaryAgent()
        try:
            app = agent.app
            app.config.set("state_embedding_enabled", False)
            app.config.set("experience_enabled", False)
            beat = app.state_heartbeat
            beat.models = _Models(delay)
            beat.room = _Room(allowed)
            # The pool decides how many go at once (common/pool.py): with the
            # real room it would be how busy this computer is, not the test.
            beat.pool.room = beat.room
            anchor = utcnow().replace(hour=0, minute=0, second=0,
                                      microsecond=0)
            count = [0]

            def write(group, title, hours):
                count[0] += 1
                began = anchor - timedelta(hours=hours)
                app.states.add(State(
                    id=f"state_{group}_{count[0]:03d}", title=title,
                    detail=f"{group} detail", starts_at=began,
                    ends_at=began + timedelta(minutes=15),
                    metadata={"path": f"C:/work/{group}"}))

            for number, (group, titles) in enumerate(GROUPS.items()):
                for index, title in enumerate(titles):
                    write(group, title, 30 + index + 10 * number)
            beat.run_once(trigger="manual_debug")
            first = len(beat.models.sent)
            for group, titles in GROUPS.items():
                write(group, titles[0].replace("They are", "They are again"), 2)
            began = time.perf_counter()
            report = beat.run_once(trigger="manual_debug")
            took = time.perf_counter() - began
            second = beat.models.sent[first:]
            # And a round with nothing new: the same plan, asked again.
            beat.run_once(trigger="manual_debug")
            third = beat.models.sent[first + len(second):]
            # Less who wrote it and when its next steps were last written:
            # both carry the moment they were written.
            moments = ("written_by", "todo_at")
            tasks = sorted(([sorted(task.members), {
                k: v for k, v in (task.described or {}).items()
                if k not in moments}] for task in Kept(app.db, "live").live()),
                key=lambda row: row[0])
            return {"second": sorted((kind, _lines(text))
                                     for kind, text in second),
                    "third": third, "tasks": tasks,
                    "took": took, "most": beat.models.most,
                    # Less who wrote it, which carries the moment it did.
                    "layers": [{k: v for k, v in ((one.get("summary") or {})
                                                  .get("described") or {}).items()
                                if k not in moments}
                               for one in report.get("layers") or []],
                    "edges": len((report.get("plan") or {}).get("edges") or [])}
        finally:
            agent.cleanup()

    def test_status_calls_run_beside_each_other_and_say_the_same(self):
        alone = self._rounds(allowed=1, delay=0.2)
        beside = self._rounds(allowed=2, delay=0.2)
        statuses = [one for one in alone["second"] if one[0] == "status"]
        self.assertGreaterEqual(len(statuses), 2)
        self.assertEqual(alone["most"], 1)
        self.assertEqual(beside["most"], 2)
        # The same questions, the same stored answers, the same picture.
        self.assertEqual(alone["second"], beside["second"])
        self.assertEqual(alone["tasks"], beside["tasks"])
        self.assertEqual(alone["layers"], beside["layers"])
        # BESIDE EACH OTHER is what `most` measured: two calls in the model at
        # once. The rounds' whole times are not compared - on a busy machine
        # (a clean CI runner, 2026-10-19: 0.93 s against 0.90 s) the rest of a
        # round moves more than the two-tenths of a second saved.

    def test_the_plan_is_not_asked_again_for_the_same_text(self):
        got = self._rounds(allowed=2)
        self.assertEqual([one for one in got["third"] if one[0] == "plan"], [])
        self.assertTrue(got["edges"])


if __name__ == "__main__":
    unittest.main()

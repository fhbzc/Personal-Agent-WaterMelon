"""What a run keeps of what was said: THE FULL TRANSCRIPT - every model call
of the builder and the reviewer, whole and laid out to be read, in numbered
plain-text files beside the live session, in the project's one folder of run
logs - a folder per run, named by when it started - locked from the agents'
own tools, out of every version, and opened from the page; the live
session's lines whole too, and the tokens counted in and out, apart,
everywhere they are shown."""

from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from support import TemporaryAgent
from test_work_runs import _TwoAgents


class _Counted(_TwoAgents):
    """The two agents, each answer with what it cost: 100 tokens in, 7 out."""

    def invoke(self, request, role=""):
        reply = super().invoke(request, role)
        reply.usage = SimpleNamespace(input_tokens=100, output_tokens=7)
        return reply


LONG_CODE = ("import json\n"
             + "".join(f"value_{n} = {n} * 2  # line {n} of a long piece of code\n"
                       for n in range(40))
             + "print('THE LAST LINE OF THE CODE')\n")


class _LongArgument(_Counted):
    """The work agent's round begins with run_python and a long piece of
    code - longer than any line of the session shows."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ran = False

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        if ("You review a work agent" not in request.system and "WORK ONE ROUND" in first
                and not self.ran and len(request.messages) == 1):
            self.ran = True
            self.asked.append(request)
            reply = self._reply("", [ToolCall(id="c0", name="run_python",
                                              arguments={"code": LONG_CODE})])
            reply.usage = SimpleNamespace(input_tokens=100, output_tokens=7)
            return reply
        return super().invoke(request, role)


def _until(condition, seconds=60.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        experience = self.app.state_heartbeat.experience
        made = experience.store.create_work_project("Write notes")
        self.unit = experience.unit(made["unit_id"])

    def tearDown(self) -> None:
        for run in list(self.app.work._running.values()):
            run.stop_now = True
            run.release(True)
        _until(lambda: not any(r.alive() for r in self.app.work._running.values()))
        self.agent.cleanup()

    def _done(self, models, instruction="write the notes"):
        self.app.work.models = models
        said = self.app.work.start(self.unit, instruction)
        self.assertNotIn("error", said, said)
        self.assertTrue(_until(lambda: not self.app.work.runs(self.unit["id"])[0]["alive"]))
        row = self.app.work.runs(self.unit["id"])[0]
        return said, row


#: A record's header block, as written: its number, whose call, the part of
#: the run, when it started and finished in local time with the offset, how
#: long it took, the model and the tokens in and out.
HEADER_BLOCK = re.compile(
    r"^={80}\nCALL #(\d+) · (BUILDER|REVIEWER) · ([^\n]+)\n"
    r"started  (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d\d:\d\d)\n"
    r"finished (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d\d:\d\d) \(([^)]+)\)\n"
    r"model (\S+) · input ([\d,]+) tokens · output ([\d,]+) tokens\n={80}$", re.M)


def _logs_of(said) -> Path:
    """The run's own folder in the project's run logs."""
    found = sorted((Path(said["folder"]) / "run_logs").glob(f"*_{said['run']}"))
    assert len(found) == 1, found
    return found[0]


def _local(started_at: str) -> str:
    from datetime import datetime
    return datetime.fromisoformat(started_at).astimezone().strftime("%Y-%m-%d_%H-%M-%S")


class TheFullTranscriptTests(_Base):

    def test_every_call_is_written_whole(self) -> None:
        models = _Counted()
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        # ONE FOLDER OF LOGS PER PROJECT, a folder per run in it, named by
        # when the run started in this computer's time, then its id.
        logs = _logs_of(said)
        self.assertEqual(logs.parent, Path(said["folder"]) / "run_logs")
        self.assertEqual(logs.name, f"{_local(row['started_at'])}_{said['run']}")
        self.assertTrue((logs.parent / "README.txt").is_file())
        self.assertIn("ONE FOLDER PER RUN", (logs.parent / "README.txt").read_text(
            encoding="utf-8"))
        self.assertTrue((logs / "events.jsonl").is_file())
        self.assertFalse((Path(said["folder"]) / "runs" / said["run"] / ".session").exists())
        files = sorted(logs.glob("transcript-*.txt"))
        self.assertEqual([one.name for one in files], ["transcript-001.txt"])
        text = files[0].read_text(encoding="utf-8")
        # One record per call, numbered in the order made, each saying whose
        # it is, when it started and finished - in order - and what it cost.
        headers = HEADER_BLOCK.findall(text)
        self.assertEqual(len(headers), len(models.asked))
        self.assertEqual([int(one[0]) for one in headers], list(range(1, len(headers) + 1)))
        self.assertEqual({one[1] for one in headers}, {"BUILDER", "REVIEWER"})
        parts = {one[2].strip() for one in headers}
        for part in ("contract", "round 1", "final review", "deck"):
            self.assertIn(part, parts)
        self.assertTrue(any(one.startswith("slides of v") for one in parts), parts)
        times = [(one[3], one[4]) for one in headers]
        self.assertTrue(all(begun <= ended for begun, ended in times))
        self.assertEqual([one[0] for one in times], sorted(one[0] for one in times),
                         "the records are in the order the calls were made")
        self.assertTrue(all(re.fullmatch(r"\d+ s", one[5]) for one in headers))
        self.assertEqual({one[6] for one in headers}, {"test-model"})
        self.assertEqual({(one[7], one[8]) for one in headers}, {("100", "7")})
        # The whole input: the system prompt, every message - the tool
        # results laid out to read - and the whole output, tool calls with
        # their arguments a key a line.
        self.assertEqual(text.count("=== INPUT ==="), len(headers))
        self.assertEqual(text.count("=== OUTPUT ==="), len(headers))
        self.assertIn("You review a work agent", text)
        self.assertIn("DRAFT THE CONTRACT", text)
        self.assertIn("[tool result for c1]\n  written: ", text)
        self.assertIn("[tool call write_file id=c1]\n  path: notes.md\n  content: hello", text)
        self.assertIn('"verdict": "approve"', text)
        # What a conversation sends again is written again, whole, and said
        # to be sent before; what is new is marked so.
        self.assertRegex(text, r"--- message 1 of \d+ · user \(sent before, in #\d{4}\) ---")
        self.assertRegex(text, r"--- message \d+ of \d+ · user · NEW ---")
        self.assertRegex(text, r"--- system prompt \(same as in #\d{4}\) ---")
        # Shown under the run, as its files - and where its logs are.
        self.assertEqual(row["transcript"], [str(files[0])])
        self.assertEqual(row["log_dir"], str(Path(said["folder"]) / "run_logs"))
        self.assertEqual(row["run_log_dir"], str(logs))
        # Not part of any version.
        head = self.app.work.version(said["run"], self.app.work.versions(said["run"])["head"])
        self.assertFalse(any(one["path"].startswith("run_logs") or "transcript" in one["path"]
                             for one in head["files"]))

    def test_a_continued_run_goes_on_numbering(self) -> None:
        from personalized_agent.agent.work.transcript import Transcript
        with tempfile.TemporaryDirectory() as folder:
            runtime = SimpleNamespace(write_text=lambda relative, text, append=False:
                                      _append(Path(folder) / relative, text))
            first = Transcript(runtime, Path(folder), "run_logs/x_r")
            first.record("worker", "contract", "m", 1, 2, "system", [{"role": "user",
                                                                     "content": "hi"}])
            again = Transcript(runtime, Path(folder), "run_logs/x_r")
            self.assertEqual(again.record("reviewer", "contract", "m", 1, 2, "s", []), 2)
            # A message holding a line like a header is not taken for one.
            again.record("worker", "round 1", "m", 1, 2, "s",
                         [{"role": "user", "content": "CALL #0999 · BUILDER · x"}])
            self.assertEqual(Transcript(runtime, Path(folder), "run_logs/x_r").seq, 3)

    def test_a_transcript_written_before_goes_on_numbering(self) -> None:
        from personalized_agent.agent.work.transcript import Transcript
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / "runs/r/.session/transcript-001.txt"
            old.parent.mkdir(parents=True)
            old.write_text("##### #0001 · 2026-10-01T00:00:00Z · worker · contract · model m"
                           " · input 1 tokens · output 1 tokens #####\n\n=== INPUT ===\n"
                           "=== OUTPUT ===\n\n##### #0002 · 2026-10-01T00:00:01Z · reviewer"
                           " · contract · model m · input 1 tokens · output 1 tokens #####\n",
                           encoding="utf-8")
            runtime = SimpleNamespace(write_text=lambda relative, text, append=False:
                                      _append(Path(folder) / relative, text))
            again = Transcript(runtime, Path(folder), "runs/r/.session")
            self.assertEqual(again.record("worker", "round 1", "m", 1, 1, "s", []), 3)
            self.assertIn("CALL #0003 · BUILDER · round 1", old.read_text(encoding="utf-8"))


def _append(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


class ReadableRecordsTests(unittest.TestCase):
    """A record reads as what was said: code as code, results laid out,
    what was sent before said so - and nothing of it left out."""

    def setUp(self) -> None:
        from personalized_agent.agent.work.transcript import Transcript
        self.dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.dir.name)
        runtime = SimpleNamespace(write_text=lambda relative, text, append=False:
                                  _append(self.folder / relative, text))
        self.transcript = Transcript(runtime, self.folder, "run_logs/2026_r")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _text(self) -> str:
        return (self.folder / "run_logs/2026_r/transcript-001.txt").read_text(encoding="utf-8")

    def test_code_and_output_are_written_as_they_read_whole(self) -> None:
        from personalized_agent.models.base import ToolCall, ToolSpec
        stdout = "".join(f"row {n}: {'x' * 50}\n" for n in range(300)) + "THE LAST ROW\n"
        result = json.dumps({"returncode": 0, "stdout": stdout, "stderr": "",
                             "changed": ["a.csv", "b.csv"], "meta": {"rows": 300}})
        asked = [{"role": "user", "content": "WORK ONE ROUND"},
                 {"role": "assistant", "content": [
                     {"type": "tool_use", "id": "c1", "name": "run_python",
                      "input": {"code": LONG_CODE, "timeout": 60}}]},
                 {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "c1", "content": result}]}]
        answer = SimpleNamespace(text="Going on.", tool_calls=[
            ToolCall(id="c2", name="write_file",
                     arguments={"path": "notes.md", "content": "line one\nline two\n"})])
        tools = [ToolSpec("run_python", "Run Python code.",
                          {"type": "object", "properties": {"code": {"type": "string"}}})]
        self.transcript.record("worker", "round 2", "m", 12345, 678, "THE SYSTEM", asked,
                               answer, tools=tools, started=time.time() - 35)
        text = self._text()
        # The code, every line of it, on lines of its own - not escaped.
        self.assertIn("[tool call run_python id=c1]\n  code:\n    import json\n", text)
        for line in LONG_CODE.splitlines():
            self.assertIn("\n    " + line + "\n", text)
        self.assertIn("  timeout: 60", text)
        self.assertNotIn("\\n", text, "no line break is written escaped")
        # The result parsed and laid out: its output whole, a line a line.
        self.assertIn("[tool result for c1]\n  returncode: 0\n  stdout:\n    row 0: ", text)
        for n in (0, 150, 299):
            self.assertIn(f"\n    row {n}: {'x' * 50}\n", text)
        self.assertIn("\n    THE LAST ROW\n", text)
        self.assertIn('  stderr: ""', text)
        self.assertIn("  changed:\n    - a.csv\n    - b.csv\n  meta:\n    rows: 300", text)
        # The answer, its tool calls laid out the same way.
        self.assertIn("=== OUTPUT ===\nGoing on.\n\n[tool call write_file id=c2]\n"
                      "  path: notes.md\n  content:\n    line one\n    line two\n", text)
        # The tools offered, defined whole.
        self.assertIn("--- tools offered (1): run_python ---\n* run_python\n"
                      "  description: Run Python code.", text)
        # The header: whose, which part, when - in this computer's time, with
        # its offset - how long, and what it cost.
        found = HEADER_BLOCK.findall(text)
        self.assertEqual(len(found), 1)
        number, who, part, began, ended, took, model, tokens_in, tokens_out = found[0]
        self.assertEqual((number, who, part, model), ("0001", "BUILDER", "round 2", "m"))
        self.assertEqual((tokens_in, tokens_out), ("12,345", "678"))
        self.assertIn(took, ("35 s", "36 s"))
        from datetime import datetime
        offset = datetime.now().astimezone().strftime("%z")
        self.assertTrue(began.endswith(f"{offset[:3]}:{offset[3:]}"), began)
        self.assertLess(began, ended)

    def test_what_was_sent_before_is_said_so_and_written_again_whole(self) -> None:
        first = [{"role": "user", "content": "THE TASK\nin two lines"}]
        self.transcript.record("worker", "round 1", "m", 1, 1, "THE SYSTEM", first)
        # The reviewer between: its own conversation.
        self.transcript.record("reviewer", "round 1", "m", 1, 1, "THE REVIEWER",
                               [{"role": "user", "content": "REVIEW IT"}])
        second = [*first, {"role": "assistant", "content": "a first try"},
                  {"role": "user", "content": "THE RESULT"}]
        self.transcript.record("worker", "round 1", "m", 1, 1, "THE SYSTEM", second)
        text = self._text()
        records = re.split(r"(?m)^={80}\nCALL #", text)[1:]
        self.assertEqual(len(records), 3)
        reviewer, again = records[1], records[2]
        self.assertTrue(reviewer.startswith("0002 · REVIEWER · round 1"))
        self.assertIn("--- message 1 of 1 · user · NEW ---\nREVIEW IT", reviewer)
        self.assertIn("(messages 1-1 of 3 were sent before, in #0001; what is new "
                      "begins at message 2)", again)
        self.assertIn("--- system prompt (same as in #0001) ---\nTHE SYSTEM", again)
        self.assertIn("--- message 1 of 3 · user (sent before, in #0001) ---\n"
                      "THE TASK\nin two lines", again)
        self.assertIn("--- message 2 of 3 · assistant · NEW ---\na first try", again)
        self.assertIn("--- message 3 of 3 · user · NEW ---\nTHE RESULT", again)

    def test_a_failed_call_says_so(self) -> None:
        self.transcript.record("reviewer", "contract", "", 0, 0, "s",
                               [{"role": "user", "content": "hi"}], error="TimeoutError: slow")
        text = self._text()
        self.assertIn("CALL #0001 · REVIEWER · contract", text)
        self.assertIn("=== OUTPUT ===\n(the call failed: TimeoutError: slow)", text)


class SplittingTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.dir.name)
        self.runtime = SimpleNamespace(write_text=lambda relative, text, append=False:
                                       _append(self.folder / relative, text))

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _files(self):
        return sorted((self.folder / "run_logs/x_r").glob("transcript-*.txt"))

    def test_a_file_is_closed_at_the_limit_and_a_record_never_split(self) -> None:
        from personalized_agent.agent.work.transcript import Transcript
        transcript = Transcript(self.runtime, self.folder, "run_logs/x_r", limit=4096)
        for n in range(12):
            transcript.record("worker", f"round {n}", "m", 10, 5, "system",
                              [{"role": "user", "content": f"message {n} " + "é" * 600}])
        files = self._files()
        self.assertGreater(len(files), 2)
        self.assertEqual([one.name for one in files],
                         [f"transcript-{n:03d}.txt" for n in range(1, len(files) + 1)])
        seen = []
        for one in files:
            data = one.read_bytes()
            self.assertLessEqual(len(data), 4096)
            text = data.decode("utf-8")
            # Each file begins with a record's header and holds whole ones.
            self.assertTrue(text.startswith("=" * 80 + "\nCALL #"), one)
            self.assertEqual(text.count("=== INPUT ==="), text.count("=== OUTPUT ==="))
            seen += [int(n) for n in re.findall(r"^CALL #(\d+) ·", text, re.M)]
        self.assertEqual(seen, list(range(1, 13)))

    def test_a_record_larger_than_a_file_is_cut_with_continued_marks(self) -> None:
        from personalized_agent.agent.work.transcript import Transcript
        transcript = Transcript(self.runtime, self.folder, "run_logs/x_r", limit=4096)
        transcript.record("worker", "contract", "m", 1, 1, "small", [])
        big = "".join(f"line {n} ü\n" for n in range(2000))
        transcript.record("worker", "round 1", "m", 1, 1, "system",
                          [{"role": "user", "content": big}])
        files = self._files()
        self.assertGreater(len(files), 3)
        texts = [one.read_bytes() for one in files]
        self.assertTrue(all(len(one) <= 4096 for one in texts))
        texts = [one.decode("utf-8") for one in texts]
        # The small record alone in the first file; the big one from the
        # second on, each part saying where it goes on and where from.
        self.assertEqual(len(re.findall(r"^CALL #", texts[0], re.M)), 1)
        self.assertIn("(record continued in transcript-003.txt)", texts[1])
        self.assertIn("(record continued from transcript-002.txt, part 2)", texts[2])
        joined = "".join(re.sub(r"\n?----- \(record continued[^)]*\) -----\n", "", one)
                         for one in texts[1:])
        self.assertIn(big, joined)


class TheProjectsLogFolderTests(_Base):

    def test_named_by_start_time_found_by_id_and_older_runs_where_they_were(self) -> None:
        from personalized_agent.agent.work.runner import log_dir_for
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            early = log_dir_for(folder, "run_b", "2026-10-09T06:32:05+00:00")
            later = log_dir_for(folder, "run_a", "2026-10-10T01:02:03+00:00")
            self.assertEqual(early.parent, folder / "run_logs")
            self.assertEqual(early.name, f"{_local('2026-10-09T06:32:05+00:00')}_run_b")
            # Sorting by name is sorting by when they started.
            self.assertEqual(sorted([later.name, early.name]), [early.name, later.name])
            # One already there is found by its id, whatever time named it.
            early.mkdir(parents=True)
            self.assertEqual(log_dir_for(folder, "run_b", "2030-01-01T00:00:00+00:00"), early)
            # A run from before keeps its logs where it kept them.
            old = folder / "runs" / "run_c" / ".session"
            old.mkdir(parents=True)
            self.assertEqual(log_dir_for(folder, "run_c", "2026-10-09T06:32:05+00:00"), old)

    def test_a_run_from_before_reads_and_goes_on_where_its_logs_were(self) -> None:
        from personalized_agent.agent.work.runner import NOT_FINISHED, WorkRuns
        models = _Counted()
        self.app.work.models = models
        said = self.app.work.start(self.unit, "write the notes",
                                   limits={"calls": 8, "minutes": 0, "tokens": 0})
        self.assertTrue(_until(lambda: not self.app.work.runs(self.unit["id"])[0]["alive"]))
        row = self.app.work.runs(self.unit["id"])[0]
        self.assertEqual(row["status"], NOT_FINISHED, row["log"][-6:])
        # As a run kept them before the project's logs had a folder: in
        # its own run folder.
        old = Path(said["folder"]) / "runs" / said["run"] / ".session"
        _logs_of(said).rename(old)
        before = (old / "transcript-001.txt").read_text(encoding="utf-8")
        events = len((old / "events.jsonl").read_text(encoding="utf-8").splitlines())
        again = WorkRuns(self.app.db, self.app.policy, models, None)
        row = again.runs(self.unit["id"])[0]
        self.assertEqual(row["transcript"], [str(old / "transcript-001.txt")])
        self.assertEqual(row["run_log_dir"], str(old))
        self.assertEqual(len(again.live(said["run"], 0)["events"]), events)
        # Continued: it goes on writing there, numbering on - nothing moved.
        continued = self.app.work.continue_run(said["run"], self.unit,
                                               {"calls": 0, "minutes": 0, "tokens": 0})
        self.assertNotIn("error", continued, continued)
        self.assertTrue(_until(lambda: not self.app.work.runs(self.unit["id"])[0]["alive"]))
        after = (old / "transcript-001.txt").read_text(encoding="utf-8")
        self.assertTrue(after.startswith(before))
        numbers = [int(n) for n in re.findall(r"^CALL #(\d+) ·", after, re.M)]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        self.assertGreater(len((old / "events.jsonl").read_text(encoding="utf-8")
                               .splitlines()), events)
        self.assertEqual(list((Path(said["folder"]) / "run_logs").glob(f"*_{said['run']}")),
                         [])

    def test_open_log_folder(self) -> None:
        import shutil
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes
        from test_work_live import _Request
        said, _row = self._done(_Counted())
        opened = []
        self.app.work.open_folder = opened.append
        routes = build_routes(self.app)
        logs = Path(said["folder"]) / "run_logs"
        # Not there yet - no run has written - and it is made, saying what
        # it is for, and opened: never the file manager itself in a test.
        shutil.rmtree(logs)
        answer = routes[("POST", "/api/work/open_logs")](_Request({"run": said["run"]}))
        self.assertEqual(answer, {"opened": str(logs)})
        self.assertEqual(opened, [logs])
        self.assertTrue((logs / "README.txt").is_file())
        with self.assertRaises(ApiError) as caught:
            routes[("POST", "/api/work/open_logs")](_Request({"run": "run_nope"}))
        self.assertEqual(caught.exception.status, 404)
        # A run whose folder is outside the application's work folder:
        # nothing is made there.
        with tempfile.TemporaryDirectory() as elsewhere:
            self.app.db.execute("UPDATE work_runs SET folder = ? WHERE id = ?",
                                (elsewhere, said["run"]))
            with self.assertRaises(ApiError) as caught:
                routes[("POST", "/api/work/open_logs")](_Request({"run": said["run"]}))
            self.assertEqual(caught.exception.status, 403)
            self.assertFalse((Path(elsewhere) / "run_logs").exists())
        self.assertEqual(len(opened), 1)


class OutOfTheVersionsTests(unittest.TestCase):

    def test_run_logs_are_no_versions_and_a_rollback_never_touches_them(self) -> None:
        from personalized_agent.agent.work.versions import (VersionError, _work_target,
                                                            restore_files, work_files)
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "notes.md").write_text("hello")
            log = folder / "run_logs" / "2026-10-09_14-32-05_run_1" / "transcript-001.txt"
            log.parent.mkdir(parents=True)
            log.write_text("CALL #0001")
            # Only at the top: a folder of the work's own by the name is work.
            (folder / "data" / "run_logs").mkdir(parents=True)
            (folder / "data" / "run_logs" / "mine.txt").write_text("the work's")
            found, _skipped = work_files(folder, "run_1")
            self.assertEqual(sorted(found), ["data/run_logs/mine.txt", "notes.md"])
            policy = SimpleNamespace(write_root=str(folder))
            import personalized_agent.runtime.filesystem as filesystem
            check = filesystem.validate_write_path
            filesystem.validate_write_path = lambda path, policy: path
            try:
                # Rolled back to a version holding nothing: the work's files
                # go, the logs stay.
                said = restore_files(SimpleNamespace(get=lambda sha: b""), {"files": {}},
                                     folder, "run_1", policy)
                with self.assertRaises(VersionError):
                    _work_target(folder, "run_1", "run_logs/x/transcript-001.txt", policy)
            finally:
                filesystem.validate_write_path = check
            self.assertEqual(sorted(said["removed"]), ["data/run_logs/mine.txt", "notes.md"])
            self.assertTrue(log.is_file())


class LockedFromTheAgentsTests(_Base):

    def test_the_agents_tools_cannot_write_or_delete_the_logs(self) -> None:
        said, _row = self._done(_Counted())
        logs = _logs_of(said)
        kept = logs / "transcript-001.txt"
        before = kept.read_text(encoding="utf-8")
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        tools = WorkTools(SandboxRuntime(self.app.work.policy_for(Path(said["folder"])),
                                         platform=self.app.platform))
        tools.locked = [Path(said["folder"]) / "run_logs"]
        relative = f"run_logs/{logs.name}/transcript-001.txt"
        refused = tools.call("write_file", {"path": relative, "content": "x"})["error"]
        self.assertIn("locked", refused)
        self.assertIn("the project's run logs - the record of what happened", refused)
        for path in (f"run_logs/{logs.name}/transcript-002.txt", "run_logs/new.txt",
                     "run_logs/README.txt"):
            self.assertIn("locked", tools.call("write_file", {"path": path,
                                                              "content": "x"})["error"])
        self.assertIn("locked", tools.call("delete_file", {"path": relative})["error"])
        self.assertIn("locked", tools.call("delete_file", {"path": "run_logs"})["error"])
        self.assertEqual(kept.read_text(encoding="utf-8"), before)
        # And the run's own tools - the builder's and the reviewer's - hold
        # the project's run logs locked, and where an older run kept its own.
        run = self.app.work._running[said["run"]]
        for held in (run.tools.locked, run.review_tools.locked):
            held = [Path(one).resolve() for one in held]
            self.assertIn((Path(said["folder"]) / "run_logs").resolve(), held)
            self.assertIn((Path(said["folder"]) / "runs" / said["run"] / ".session").resolve(),
                          held)
        self.assertIn("locked", run.tools.call("write_file", {"path": relative,
                                                              "content": "x"})["error"])
        self.assertEqual(kept.read_text(encoding="utf-8"), before)


class TokensInAndOutTests(_Base):

    def test_counted_apart_everywhere(self) -> None:
        models = _Counted()
        said, row = self._done(models)
        calls = len(models.asked)
        total = row["plan"]["total"]
        self.assertEqual((total["calls"], total["tokens_in"], total["tokens_out"]),
                         (calls, 100 * calls, 7 * calls))
        live = self.app.work.live(said["run"], 0)
        used = live["progress"]["used"]
        self.assertEqual((used["tokens_in"], used["tokens_out"], used["tokens"]),
                         (100 * calls, 7 * calls, 107 * calls))
        turns = [e for e in live["events"] if e["kind"] == "turn"]
        self.assertTrue(turns)
        self.assertTrue(all((e["detail"]["tokens_in"], e["detail"]["tokens_out"]) == (100, 7)
                            for e in turns))
        report = (Path(said["folder"]) / "runs" / said["run"] / "report.md").read_text(
            encoding="utf-8")
        self.assertRegex(report, r"\d+ tokens \(\d+ in, \d+ out\)")
        versions = self.app.work.versions(said["run"])["versions"]
        last = versions[-1]["summary"]
        self.assertGreater(last["tokens_in"], 0)
        self.assertEqual(last["tokens_in"] * 7, last["tokens_out"] * 100)
        self.assertLessEqual(versions[0]["summary"]["tokens_in"], last["tokens_in"])


class EveryLineWholeTests(_Base):

    def test_a_long_tool_argument_is_kept_whole(self) -> None:
        said, row = self._done(_LongArgument())
        self.assertEqual(row["status"], "done", row["log"][-6:])
        events = self.app.work.live(said["run"], 0)["events"]
        ran = next(e for e in events if e["kind"] == "tool"
                   and e["detail"].get("tool") == "run_python")
        # The line is a preview; the whole of it is kept with it - the code
        # as written, every line of it, and what it gave back.
        self.assertLessEqual(len(ran["text"]), 400)
        full = ran["detail"]["full"]
        self.assertIn(LONG_CODE, full)
        self.assertIn("THE LAST LINE OF THE CODE", full)
        self.assertIn("returncode", full)
        # A model's answer in full too, and the reviewer's verdicts.
        turn = next(e for e in events if e["kind"] == "turn" and e["who"] == "worker"
                    and "DRAFT" not in e["text"] and "full" in e.get("detail", {}))
        self.assertTrue(turn["detail"]["full"])
        # Read back from the run's folder the same.
        from personalized_agent.agent.work.runner import read_session
        kept = read_session(_logs_of(said))
        self.assertIn(LONG_CODE, next(e for e in kept if e["seq"] == ran["seq"])
                      ["detail"]["full"])

    def test_a_long_note_is_kept_whole(self) -> None:
        from personalized_agent.agent.work.runner import _note_as_event
        comments = [f"comment {n}: " + "a long reason " * 30 for n in range(6)]
        entry = {"what": "contract reviewed", "verdict": "revise", "comments": comments}
        short = _note_as_event(entry)
        whole = _note_as_event(entry, whole=True)
        self.assertNotIn(comments[5], short[2])
        for one in comments:
            self.assertIn(one, whole[2])


if __name__ == "__main__":
    unittest.main()

"""Listeners and model calls made cheaper without changing what they give.

Each change here is only worth having if what comes out is what came out
before: the same summary of a transcript, the same browser visits, the same
events kept, the same measures of a window, the same answer from a model.
So each is tested against the way it used to be done.
"""

import json
import os
import random
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


class _Config:
    """Just enough of the settings for a provider and its filter."""

    def __init__(self, values: Dict[str, Any] = None) -> None:
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def lines(self, key):
        raw = self.values.get(key, "")
        if isinstance(raw, (list, tuple)):
            return [str(item) for item in raw if str(item).strip()]
        return [line.strip() for line in str(raw).splitlines() if line.strip()]

    def set(self, key, value):
        self.values[key] = value


# -- a transcript that grows is read from where it was left -------------------
def _summarise_as_it_was(path: Path):
    """The summary exactly as it was computed before: the whole file, every time."""
    first = last = project = None
    records = 0
    session_id = path.stem
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                stamp = entry.get("timestamp")
                if isinstance(stamp, str) and stamp:
                    first = stamp if first is None or stamp < first else first
                    last = stamp if last is None or stamp > last else last
                identifier = entry.get("sessionId")
                if isinstance(identifier, str) and identifier:
                    session_id = identifier
                if project is None:
                    where = entry.get("cwd")
                    if isinstance(where, str) and where:
                        project = where
                records += 1
    except OSError:
        return None
    if not records or last is None:
        return None
    return {"session_id": session_id, "started_at": first, "last_activity": last,
            "records": records, "project": project, "path": str(path)}


def _transcript_bytes(lines: int, seed: int) -> bytes:
    """A transcript with everything a real one has, and some things it should not."""
    rng = random.Random(seed)
    out = []
    for index in range(lines):
        kind = rng.random()
        if kind < 0.05:
            out.append(b"")                                   # a blank line
            continue
        if kind < 0.08:
            out.append(b"{not json")                           # a torn line
            continue
        if kind < 0.10:
            out.append(b"[1, 2, 3]")                           # JSON, not a record
            continue
        entry = {"type": "user", "message": {"content": "é中文 " * rng.randint(0, 40)}}
        if rng.random() < 0.9:
            stamp = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(
                seconds=rng.randint(0, 10 ** 6))
            entry["timestamp"] = stamp.isoformat().replace("+00:00", "Z")
        if rng.random() < 0.5:
            entry["sessionId"] = f"s-{rng.randint(0, 3)}"
        if index > 3 and rng.random() < 0.3:
            entry["cwd"] = f"C:/work/project-{rng.randint(0, 3)}"
        raw = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        if rng.random() < 0.02:
            raw += b"\xff\xfe"                                 # bytes that are not UTF-8
        out.append(raw)
    ending = [b"\n", b"\r\n"]
    return b"".join(line + ending[rng.random() < 0.2] for line in out)


class ATranscriptThatGrowsIsReadFromWhereItWasLeftTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.listener.providers.assistant import AssistantProvider

        self.dir = Path(tempfile.mkdtemp(prefix="pa-grow-"))
        self.provider = AssistantProvider(None)
        # Small pieces, so the seams between them are crossed many times.
        self.provider.BLOCK = 97
        self.path = self.dir / "session-one.jsonl"

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _agrees(self, note: str) -> Dict[str, Any]:
        expected = _summarise_as_it_was(self.path)
        got = self.provider._summary_of(self.path)
        self.assertEqual(got, expected, note)
        return got

    def test_grown_in_many_steps_it_says_what_a_whole_reading_says(self) -> None:
        for seed in range(6):
            data = _transcript_bytes(300, seed)
            self.path.write_bytes(b"")
            self.provider._summaries.clear()
            self.provider._tallies.clear()
            rng = random.Random(100 + seed)
            written = 0
            while written < len(data):
                # Any byte at all: inside a line, inside a character, between
                # the \r and the \n of one ending.
                step = rng.randint(1, 900)
                with self.path.open("ab") as handle:
                    handle.write(data[written:written + step])
                written += step
                self._agrees(f"seed {seed}, {written} bytes")

    def test_a_last_line_without_its_newline_counts_now_and_once_later(self) -> None:
        line = json.dumps({"timestamp": "2026-09-20T09:00:00Z", "sessionId": "a"})
        self.path.write_text(line + "\n" + line.replace("09:00", "10:00"),
                             encoding="utf-8")
        self.assertEqual(self._agrees("unfinished")["records"], 2)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        self.assertEqual(self._agrees("finished")["records"], 2)

    def test_and_only_the_new_part_is_read(self) -> None:
        line = json.dumps({"timestamp": "2026-09-20T09:00:00Z", "sessionId": "a"}) + "\n"
        self.path.write_text(line * 2000, encoding="utf-8")
        self._agrees("first")
        folded = []
        original = self.provider._fold

        def counted(handle, tally):
            text = handle.read()
            folded.append(len(text))
            import io
            original(io.StringIO(text), tally)

        self.provider._fold = counted
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line.replace("09:00", "11:00"))
        self.assertEqual(self._agrees("grown")["records"], 2001)
        self.assertEqual(sum(folded), len(line), "more than the new line was read")

    def test_a_file_that_shrank_or_was_replaced_is_read_again(self) -> None:
        one = json.dumps({"timestamp": "2026-09-20T09:00:00Z", "sessionId": "a"}) + "\n"
        two = json.dumps({"timestamp": "2026-09-21T09:00:00Z", "sessionId": "b"}) + "\n"
        self.path.write_text(one * 5, encoding="utf-8")
        self._agrees("first")
        self.path.write_text(two * 3, encoding="utf-8")            # shorter
        self.assertEqual(self._agrees("shrank")["session_id"], "b")
        # Longer, with different bytes where the old end was.
        self.path.write_text(one * 4 + two * 2, encoding="utf-8")
        self._agrees("replaced")
        # The same length, different content: the seam differs.
        stat = self.path.stat()
        self.path.write_text(two * 4 + one * 2, encoding="utf-8")
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10 ** 9))
        self._agrees("rewritten in place")

    def test_a_transcript_that_disappears_is_forgotten(self) -> None:
        self.path.write_text(json.dumps({"timestamp": "2026-09-20T09:00:00Z"}) + "\n",
                             encoding="utf-8")
        self._agrees("there")
        self.path.unlink()
        self.assertIsNone(self.provider._summary_of(self.path))
        self.assertNotIn(str(self.path), self.provider._tallies)


# -- a history database that has not changed is not copied --------------------
class AnUnchangedHistoryIsNotCopiedTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.listener.providers.browser import BrowserProvider

        self.dir = Path(tempfile.mkdtemp(prefix="pa-hist-"))
        self.db = self.dir / "History"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE urls (url TEXT, title TEXT, last_visit_time INTEGER)")
        conn.commit()
        conn.close()
        paths = {"Chrome": str(self.db)}

        class _Activity:
            def get_browser_history_paths(self):
                return paths

        class _Platform:
            activity = _Activity()

        self.platform = _Platform()
        self.make = lambda: BrowserProvider(_Config(), self.platform)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _visit(self, url: str, minutes_ago: float) -> None:
        when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        micro = int((when - datetime(1601, 1, 1, tzinfo=timezone.utc))
                    / timedelta(microseconds=1))
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (url, url, micro))
        conn.commit()
        conn.close()

    def _run(self, provider, copies: List[int]):
        sent = []
        provider.bind(sent.append)
        original = provider._copy_and_query

        def counted(browser, path):
            copies.append(1)
            return original(browser, path)

        provider._copy_and_query = counted
        return sent

    def test_the_same_visits_with_far_fewer_copies(self) -> None:
        new_copies: List[int] = []
        new = self.make()
        new_sent = self._run(new, new_copies)
        old_copies: List[int] = []
        old = self.make()
        old.REREAD_AT_LEAST_EVERY = -1.0          # copy every time, as it was
        old_sent = self._run(old, old_copies)

        self._visit("https://a.example/", 2)
        for provider in (new, old):
            provider.poll()
        for _ in range(5):                          # nothing changes
            for provider in (new, old):
                provider.poll()
        self._visit("https://b.example/", 1)
        stat = self.db.stat()
        os.utime(self.db, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10 ** 9))
        for provider in (new, old):
            provider.poll()
            provider.poll()

        def seen(events):
            return [(e.data["url"], e.timestamp) for e in events]

        self.assertEqual(seen(new_sent), seen(old_sent))
        self.assertEqual([url for url, _ in seen(new_sent)],
                         ["https://a.example/", "https://b.example/"])
        self.assertEqual(len(new_copies), 2, "copied although nothing had changed")
        self.assertEqual(len(old_copies), 8)
        self.assertEqual(new.get_status()["browsers"], {"Chrome": "ok"})

    def test_a_copy_that_failed_is_tried_again(self) -> None:
        provider = self.make()
        copies: List[int] = []
        self._run(provider, copies)
        provider._copy_and_query = lambda browser, path: (copies.append(1), None)[1]
        provider.poll()
        provider.poll()
        self.assertEqual(len(copies), 2)


# -- an event the manager will drop is not read inside -------------------------
class _Raw:
    def __init__(self, path, event_type="modified"):
        self.src_path = str(path)
        self.event_type = event_type
        self.is_directory = False


class _Store:
    def __init__(self):
        self.events = []

    def add_event(self, event):
        self.events.append(event)
        return event


class WhatTheManagerDropsIsNotOpenedTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.listener.core.manager import ListenerManager
        from personalized_agent.listener.providers import filesystem

        self.module = filesystem
        self.dir = Path(tempfile.mkdtemp(prefix="pa-drop-")).resolve()
        self.config = _Config({
            "listener_enabled": True,
            "listener_filesystem_enabled": True,
            "listener_only_while_present": False,
            "listener_only_what_the_user_did": False,
            "listener_excluded_names": "node_modules\n.git",
            "listener_excluded_file_patterns": "*.log",
            "listener_directory_exclusions": str(self.dir / "skipped"),
        })
        (self.dir / "node_modules").mkdir()
        (self.dir / "skipped").mkdir()
        self.files = [
            self.dir / "notes.md",                       # kept, and read
            self.dir / "node_modules" / "index.js",      # an ignored folder
            self.dir / "skipped" / "plan.md",            # an excluded directory
            self.dir / "build.log",                      # an ignored file name
        ]
        for path in self.files:
            path.write_text(f"the text of {path.name}", encoding="utf-8")
        self.manager = ListenerManager
        self.opened: List[str] = []

    def tearDown(self) -> None:
        if "open" in vars(self.module):
            del self.module.open
        shutil.rmtree(self.dir, ignore_errors=True)

    def _record(self, early: bool):
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        store = _Store()
        manager = self.manager(config=self.config, raw_store=store)
        provider = FilesystemProvider(self.config)
        manager.register(provider)
        if not early:
            provider._settings_refuse = lambda path: False
        opened = []

        def counting_open(path, *args, **kwargs):
            opened.append(os.path.basename(str(path)))
            return open(path, *args, **kwargs)

        self.module.open = counting_open
        try:
            for path in self.files:
                provider.handle_raw(_Raw(path))
            provider.flush_held_events()
        finally:
            del self.module.open
        kept = [(e.event_type, e.data.get("path"), e.data.get("content"))
                for e in store.events]
        return kept, opened

    def test_the_same_events_are_kept_and_only_the_kept_file_is_opened(self) -> None:
        early, opened_early = self._record(early=True)
        late, opened_late = self._record(early=False)
        self.assertEqual(early, late)
        self.assertEqual([path for _, path, _ in early], [str(self.files[0])])
        self.assertEqual(early[0][2], "the text of notes.md")
        self.assertEqual(opened_early, ["notes.md"])
        self.assertEqual(sorted(opened_late),
                         sorted(["notes.md", "index.js", "plan.md", "build.log"]))

    def test_a_provider_not_bound_to_the_manager_reads_as_before(self) -> None:
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        provider = FilesystemProvider(self.config)
        sent = []
        provider.bind(sent.append)
        for path in self.files:
            provider.handle_raw(_Raw(path))
        provider.flush_held_events()
        by_name = {os.path.basename(e.data["path"]): e.data.get("content")
                   for e in sent}
        self.assertEqual(by_name["index.js"], "the text of index.js")


# -- how colourful a window is, counted by colour ------------------------------
def _spread_as_it_was(small):
    pixels = list(small.getdata())
    rg = [r - g for r, g, _b in pixels]
    yb = [0.5 * (r + g) - b for r, g, b in pixels]

    def spread(values):
        mean = sum(values) / len(values)
        return mean, (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5

    m_rg, s_rg = spread(rg)
    m_yb, s_yb = spread(yb)
    return m_rg, s_rg, m_yb, s_yb


class ColourfulnessCountedByColourTests(unittest.TestCase):

    def test_the_same_numbers_as_counting_every_pixel(self) -> None:
        from PIL import Image, ImageDraw

        from personalized_agent.listener.providers.screenshot import (
            _colour_spread, measures)

        rng = random.Random(3)
        pictures = [Image.new("RGB", (64, 40), colour)
                    for colour in [(0, 0, 0), (255, 255, 255), (1, 0, 0), (0, 0, 1)]]
        for index in range(60):
            width, height = rng.randint(1, 330), rng.randint(1, 330)
            if index % 3 == 0:
                picture = Image.frombytes("RGB", (width, height),
                                          bytes(rng.randrange(256)
                                                for _ in range(width * height * 3)))
            else:
                picture = Image.new("RGB", (width, height),
                                    tuple(rng.randrange(256) for _ in range(3)))
                draw = ImageDraw.Draw(picture)
                for _ in range(rng.randint(0, 30)):
                    x, y = rng.randrange(width), rng.randrange(height)
                    draw.rectangle([x, y, x + rng.randint(1, 90), y + rng.randint(1, 40)],
                                   fill=tuple(rng.randrange(256) for _ in range(3)))
            pictures.append(picture)
        for picture in pictures:
            small = picture.convert("RGB")
            small.thumbnail((320, 320))
            was = _spread_as_it_was(small)
            now = _colour_spread(small)
            # The means are computed exactly as they were.
            self.assertEqual(now[0], was[0])
            self.assertEqual(now[2], was[2])
            for a, b in ((now[1], was[1]), (now[3], was[3])):
                self.assertAlmostEqual(a, b, delta=1e-9 * max(1.0, abs(b)))
            colourful = (was[1] ** 2 + was[3] ** 2) ** 0.5 + 0.3 * (
                was[0] ** 2 + was[2] ** 2) ** 0.5
            self.assertEqual(measures(picture)["colourful"], round(colourful, 1))


# -- prompt caching, and the cached input counted -----------------------------
class _Usage:
    def __init__(self, **figures):
        self.__dict__.update(figures)


class _Message:
    def __init__(self):
        class _Text:
            type = "text"
            text = "the answer"

        self.content = [_Text()]
        self.stop_reason = "end_turn"
        self.usage = _Usage(input_tokens=12, output_tokens=7,
                            cache_read_input_tokens=9000,
                            cache_creation_input_tokens=300)


class _Messages:
    def __init__(self, refuse: bool = False):
        self.calls = []
        self.refuse = refuse

    def create(self, *, model, max_tokens, messages, system=None, tools=None,
               thinking=None, cache_control=None):
        self.calls.append({"system": system, "cache_control": cache_control,
                           "messages": messages, "tools": tools})
        if self.refuse and cache_control is not None:
            error = Exception("cache_control: Extra inputs are not permitted")
            error.status_code = 400
            raise error
        return _Message()


class _OldMessages(_Messages):
    """An SDK from before the top-level parameter."""

    def create(self, *, model, max_tokens, messages, system=None, tools=None,
               thinking=None):
        return super().create(model=model, max_tokens=max_tokens, messages=messages,
                              system=system, tools=tools, thinking=thinking)


class _Client:
    def __init__(self, messages):
        self.messages = messages


class _Credentials:
    def get(self, key):
        return None

    def has(self, key):
        return False


class PromptCachingTests(unittest.TestCase):

    def _provider(self, messages):
        from personalized_agent.models.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(_Credentials())
        client = _Client(messages)
        provider._get_client = lambda: client
        return provider

    def _request(self):
        from personalized_agent.models.base import ModelRequest

        return ModelRequest(messages=[{"role": "user", "content": "hello"}],
                            system="You are the agent.")

    def test_caching_is_asked_for_and_the_prompt_is_unchanged(self) -> None:
        messages = _Messages()
        response = self._provider(messages).invoke(self._request(), "claude-opus-5")
        call = messages.calls[-1]
        self.assertEqual(call["cache_control"], {"type": "ephemeral"})
        self.assertEqual(call["system"], "You are the agent.")
        self.assertEqual(call["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(response.text, "the answer")
        self.assertEqual(response.usage.input_tokens, 12)
        self.assertEqual(response.usage.cached_input_tokens, 9000)
        self.assertEqual(response.usage.cache_written_tokens, 300)

    def test_an_older_sdk_is_asked_on_the_system_prompt(self) -> None:
        messages = _OldMessages()
        self._provider(messages).invoke(self._request(), "claude-opus-5")
        self.assertEqual(messages.calls[-1]["system"], [
            {"type": "text", "text": "You are the agent.",
             "cache_control": {"type": "ephemeral"}}])

    def test_an_endpoint_that_refuses_it_is_answered_without_it(self) -> None:
        messages = _Messages(refuse=True)
        provider = self._provider(messages)
        response = provider.invoke(self._request(), "claude-opus-5")
        self.assertEqual(response.text, "the answer")
        self.assertEqual([c["cache_control"] for c in messages.calls],
                         [{"type": "ephemeral"}, None])
        provider.invoke(self._request(), "claude-opus-5")
        self.assertIsNone(messages.calls[-1]["cache_control"], "asked again")

    def test_the_cached_input_reaches_the_recorder_that_takes_it(self) -> None:
        from personalized_agent.models.manager import _takes

        def new_recorder(*, provider, model, task, input_tokens, output_tokens,
                         latency_ms, ok=True, error=None, estimated_cost=None,
                         cached_input_tokens=0, cache_written_tokens=0):
            pass

        def old_recorder(*, provider, model, task, input_tokens, output_tokens,
                         latency_ms, ok=True, error=None, estimated_cost=None):
            pass

        self.assertTrue(_takes(new_recorder, "cached_input_tokens"))
        self.assertFalse(_takes(old_recorder, "cached_input_tokens"))
        self.assertTrue(_takes(lambda **anything: None, "cached_input_tokens"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()

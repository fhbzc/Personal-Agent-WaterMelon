"""Each version of a run carries its SLIDES: where the whole work stands at
that version - the goal, what is done with its evidence, what is new since
the version before, what remains - written by the work agent in one model
call that is given the deck of the version before to build on, counted and
transcribed like any other; and shown, opened in the tree, as slides."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from support import TemporaryAgent
from test_work_transcript import _Base, _Counted


class _Slides(_Counted):
    """The two agents, the work agent writing each version's slides - or,
    `failing`, unable to: the model is not there for them."""

    def __init__(self, failing=False, **kwargs):
        super().__init__(**kwargs)
        self.failing = failing
        self.slides = []

    def invoke(self, request, role=""):
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        if "MAKE THE PROGRESS SLIDES" in first:
            self.asked.append(request)
            self.slides.append(first)
            if self.failing:
                raise RuntimeError("the model is not available")
            n = len(self.slides)
            reply = self._reply(json.dumps({
                "title": f"Where the notes stand {n}",
                "summary": f"After {n} key points the notes exist.",
                "done": [{"what": "notes.md written", "evidence": "notes.md line 1"}],
                "new": [f"what is new at point {n}"],
                "results": [{"title": "The greeting", "shows": "what it says",
                             "figure": "no-such-figure.png",
                             "variables": [{"name": "greeting", "meaning": "the word",
                                            "value": "hello", "source": "notes.md"}]}],
                "remaining": ["nothing much"], "problems": ["none known"],
                "next": [f"next step {n}"]}))
            from types import SimpleNamespace
            reply.usage = SimpleNamespace(input_tokens=100, output_tokens=7)
            return reply
        return super().invoke(request, role)


class EachVersionsSlidesTests(_Base):

    def test_one_call_per_version_building_on_the_one_before(self) -> None:
        models = _Slides()
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        tree = self.app.work.versions(said["run"])
        versions = tree["versions"]
        # One call per version, each made - but the contract's: there is no
        # work to show yet.
        self.assertEqual(versions[0]["kind"], "contract")
        self.assertEqual(versions[0]["summary"]["slides"], "skipped")
        self.assertEqual(len(models.slides), len(versions) - 1)
        self.assertEqual({one["summary"]["slides"] for one in versions[1:]}, {"made"})
        # The first has no deck before it; each after is given the one
        # before, to build on.
        self.assertIn("(none - no version before this one has slides)", models.slides[0])
        for n in range(1, len(models.slides)):
            self.assertIn(f"Where the notes stand {n}", models.slides[n])
            self.assertIn(f"THE DECK OF v{n + 1}", models.slides[n])
        # The whole work is its subject: its contract and its files.
        self.assertIn("notes.md says hello", models.slides[-1])
        self.assertIn("added: notes.md", "\n".join(models.slides))
        # Counted in the run's use, in and out.
        total = row["plan"]["total"]
        self.assertEqual(total["calls"], len(models.asked))
        self.assertEqual(total["tokens_out"], 7 * len(models.asked))
        # In the transcript.
        session = next((Path(said["folder"]) / "run_logs").glob(f"*_{said['run']}"))
        text = (session / "transcript-001.txt").read_text(encoding="utf-8")
        self.assertEqual(text.count("MAKE THE PROGRESS SLIDES FOR VERSION"), len(versions) - 1)
        # Kept in the version, and served as slides by the deck's renderer.
        head = self.app.work.version(said["run"], tree["head"])
        slides = head["slides"]
        self.assertEqual(slides["status"], "made")
        page = slides["page"]
        self.assertIn('<section class="slide">', page)
        self.assertEqual(page.count('<section class="slide">'), slides["count"])
        for said_in_it in (f"Where the notes stand {len(versions) - 1}", "Done so far",
                           "notes.md line 1", "New since", "What remains",
                           "next step", "The greeting", "id=\"where\""):
            self.assertIn(said_in_it, page)
        # A figure that is not there is left out.
        self.assertNotIn("no-such-figure.png", page)
        # The first version's slides are its own, kept as they were; the
        # contract's version says it has none, and why.
        first = self.app.work.version(said["run"], "v2")["slides"]
        self.assertIn("Where the notes stand 1", first["page"])
        self.assertEqual(self.app.work.version(said["run"], "v1")["slides"],
                         {"status": "skipped"})

    def test_without_the_model_the_version_is_saved_and_says_so(self) -> None:
        said, row = self._done(_Slides(failing=True))
        self.assertEqual(row["status"], "done", row["log"][-6:])
        versions = self.app.work.versions(said["run"])["versions"]
        self.assertGreaterEqual(len(versions), 4)
        self.assertEqual({one["summary"]["slides"] for one in versions[1:]}, {"not made"})
        # WHY, kept with each - not "the model could not be asked" for all.
        detail = self.app.work.version(said["run"], "v2")
        self.assertEqual(detail["slides"]["status"], "not made")
        self.assertTrue(detail["slides"]["why"])

    def test_a_version_saved_before_slides_has_none(self) -> None:
        said, _row = self._done(_Slides())
        from personalized_agent.agent.work.versions import VersionStore
        store = VersionStore(Path(said["folder"]) / "runs" / said["run"])
        manifest = store.manifest("v1")
        state = store.get_json(manifest["state"])
        state.pop("slides")
        manifest["summary"].pop("slides")
        manifest["state"] = store.put_json(state)
        (store.root / "versions" / "v1.json").write_text(json.dumps(manifest),
                                                         encoding="utf-8")
        self.assertEqual(self.app.work.version(said["run"], "v1")["slides"],
                         {"status": "none"})


#: A small PNG, drawn by code the builder runs - no package needed.
DRAW_PNG = r'''
import struct, zlib
def chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
w, h = 32, 18
raw = b''.join(b'\x00' + bytes([47, 93, 138]) * w for _ in range(h))
png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
open('fig_progress.png', 'wb').write(png)
try:
    open(ESCAPE_TO, 'w').write('no')
    print('ESCAPED')
except Exception as exc:
    print('REFUSED', type(exc).__name__)
'''

FLOW_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90">'
            '<rect width="160" height="90" fill="#2f5d8a"/></svg>')


class _Draws(_Counted):
    """The two agents - the builder DRAWING each version's slides: it runs
    code that draws a PNG into its slides folder, writes an SVG there, tries
    to write outside it (and is refused), then answers with a slide spec of
    many layouts - one image of it outside the work's folder, one missing.
    `bad`: every image it names is one that may not be shown - its slides
    are then made in words. `commit`: each round's answer says what it
    changed, as a commit."""

    def __init__(self, outside="", bad=False, commit=True, **kwargs):
        super().__init__(**kwargs)
        self.outside, self.bad, self.commit = outside, bad, commit
        self.drawn, self.worded, self.results = [], [], []

    def _summary(self, first):
        said = super()._summary(first)
        if self.commit:
            said["commit"] = {"title": "Write the greeting into notes.md",
                              "body": ["notes.md: says hello", "one line, plainly"]}
        return said

    def _counted(self, reply):
        reply.usage = SimpleNamespace(input_tokens=100, output_tokens=7)
        return reply

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        if "MAKE THE PROGRESS SLIDES FOR VERSION" in first and "IN WORDS" in first:
            self.asked.append(request)
            self.worded.append(first)
            return self._counted(self._reply(json.dumps({
                "title": f"In words {len(self.worded)}", "summary": "notes.md exists.",
                "done": [{"what": "notes.md written", "evidence": "notes.md"}],
                "next": ["a title"]})))
        if "MAKE THE PROGRESS SLIDES FOR VERSION" not in first:
            return super().invoke(request, role)
        self.asked.append(request)
        folder = Path(re.search(r"THE ONLY PLACE YOU CAN WRITE NOW: (.+)", first).group(1).strip())
        work = folder.parents[3]
        if len(request.messages) == 1:
            self.drawn.append(first)
            code = DRAW_PNG.replace("ESCAPE_TO", repr(str(work / "escaped.txt")))
            return self._counted(self._reply("", [
                ToolCall(id="s1", name="run_python", arguments={"code": code}),
                ToolCall(id="s2", name="write_file", arguments={
                    "path": str(folder / "fig_flow.svg"), "content": FLOW_SVG}),
                ToolCall(id="s3", name="write_file", arguments={
                    "path": str(work / "stray.md"), "content": "not here"}),
                ToolCall(id="s4", name="web_search", arguments={"query": "anything"})]))
        self.results.append(json.dumps(request.messages[-1]["content"]))
        n = len(self.drawn)
        if self.bad:
            slides = [{"layout": "chart", "title": "Outside", "image": self.outside},
                      {"layout": "image", "title": "Missing", "image": "nowhere.png"},
                      {"layout": "bullets", "title": "Words", "points": ["only words"]}]
        else:
            slides = [
                {"layout": "title", "title": f"The notes, drawn {n}", "subtitle": "notes.md exists"},
                {"layout": "chart", "title": "Progress", "image": str(folder / "fig_progress.png"),
                 "caption": "drawn by the builder",
                 "notes": {"shows": "How many lines notes.md has, round by round",
                           "terms": [{"term": "notes.md", "means": "the file the work writes"}],
                           "read": "Rounds go left to right; the bar's height is the lines",
                           "takeaway": "It grew each round"}},
                {"layout": "image+text", "title": "The flow", "image": "fig_flow.svg",
                 "points": ["one", "two"], "side": "right"},
                {"layout": "diagram", "title": "How it fits",
                 "nodes": [{"id": "a", "label": "Draft"}, {"id": "b", "label": "Review"},
                           {"id": "c", "label": "Notes"}],
                 "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c", "label": "ok"}]},
                {"layout": "metrics", "title": "Numbers",
                 "metrics": [{"value": "1", "label": "file"}, {"value": "5", "label": "bytes"}]},
                {"layout": "timeline", "title": "So far",
                 "milestones": [{"when": "v1", "what": "Contract", "done": True},
                                {"when": "next", "what": "Title", "done": False}]},
                {"layout": "chart", "title": "Outside", "image": self.outside},
                {"layout": "two-images", "title": "Two", "images": [
                    {"image": str(folder / "fig_progress.png"), "caption": "kept"},
                    {"image": "nowhere.png", "caption": "missing"}]}]
        return self._counted(self._reply(json.dumps({"title": f"The notes, drawn {n}",
                                                     "slides": slides})))


class _BrokenSpec(_Draws):
    """The builder drawing, then answering with a slide spec that does not
    read - a brace too many, as a deck of fifteen slides had. Asked to
    correct it, it does (`mends`) or does not. And drawing a version's
    slides takes its time (`slides_seconds`), so the work going on meanwhile
    can be seen to: when each version's slides began and ended, when every
    other call began, and what the copy of the version they were drawn
    from held."""

    def __init__(self, mends=True, slides_seconds=0.0, **kwargs):
        super().__init__(**kwargs)
        self.mends, self.slides_seconds = mends, slides_seconds
        self.fixes, self.spans, self.other_starts, self.copies = [], [], [], []

    def invoke(self, request, role=""):
        import time
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        if "MAKE THE PROGRESS SLIDES FOR VERSION" in first and "IN WORDS" not in first:
            last = request.messages[-1]["content"]
            if isinstance(last, str) and "is not valid JSON" in last:
                self.asked.append(request)
                self.fixes.append(last)
                n = len(self.drawn)
                if not self.mends:
                    return self._counted(self._reply('{"title": "still broken", "slides": [}'))
                return self._counted(self._reply(json.dumps({"title": f"Mended {n}", "slides": [
                    {"layout": "title", "title": f"Mended {n}", "subtitle": "read now"},
                    {"layout": "chart", "title": "Progress", "image": str(Path(re.search(
                        r"THE ONLY PLACE YOU CAN WRITE NOW: (.+)", first).group(1).strip())
                        / "fig_progress.png")}]})))
            if len(request.messages) == 1:
                self.spans.append([time.monotonic(), None])
                copy = re.search(r"a copy of its files is in (.+?)\. Read", first)
                self.copies.append(sorted(one.name for one in Path(copy.group(1)).iterdir())
                                   if copy else None)
                time.sleep(self.slides_seconds)
            said = super().invoke(request, role)
            if len(request.messages) > 1:
                # The spec: a slide closed twice.
                said.text = said.text.replace('"subtitle": "notes.md exists"}',
                                              '"subtitle": "notes.md exists"}}', 1)
                if self.spans and self.spans[-1][1] is None:
                    self.spans[-1][1] = time.monotonic()
            return said
        if "MAKE THE PROGRESS SLIDES FOR VERSION" not in first:
            self.other_starts.append(time.monotonic())
        return super().invoke(request, role)


class BrokenSlideSpecsAndSlidesBehindTests(_Base):
    """A slide spec that is not valid JSON is sent back once to be corrected;
    not corrected, the slides are made in words, and the version says why.
    And a version's slides are drawn behind the work - from a copy of the
    version's files - while the next step goes on without waiting for them."""

    def setUp(self) -> None:
        super().setUp()
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        self.outside = outside / "outside.png"
        self.outside.write_bytes(b"\x89PNG\r\n\x1a\nnot really")

    def test_corrected_once_then_drawn(self) -> None:
        models = _BrokenSpec(outside=str(self.outside))
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        versions = self.app.work.versions(said["run"])["versions"]
        self.assertEqual({one["summary"]["slides"] for one in versions[1:]}, {"made"})
        self.assertEqual(len(models.fixes), len(versions) - 1, "one correction each")
        self.assertIn("line", models.fixes[0])
        self.assertEqual(models.worded, [])
        page = self.app.work.version(said["run"], "v2")["slides"]["page"]
        self.assertIn("Mended 1", page)

    def test_not_corrected_then_in_words_saying_why(self) -> None:
        models = _BrokenSpec(outside=str(self.outside), mends=False)
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        versions = self.app.work.versions(said["run"])["versions"]
        self.assertEqual({one["summary"]["slides"] for one in versions[1:]}, {"made"})
        self.assertEqual(len(models.worded), len(versions) - 1)
        slides = self.app.work.version(said["run"], "v2")["slides"]
        self.assertEqual(slides["format"], "words")
        self.assertIn("not valid JSON", slides["fallback"])
        self.assertIn("not valid JSON", models.worded[0])

    def test_the_next_step_does_not_wait_for_the_slides(self) -> None:
        models = _BrokenSpec(outside=str(self.outside), slides_seconds=2.0)
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        versions = self.app.work.versions(said["run"])["versions"]
        rounds = [one for one in versions if one["kind"] == "round"]
        self.assertTrue(rounds)
        # Every version's slides were drawn, and added to it, by the end.
        self.assertEqual({one["summary"]["slides"] for one in versions[1:]}, {"made"})
        self.assertEqual(self.app.work.version(said["run"], "v2")["slides"]["status"], "made")
        # While a version's slides were drawn, the work went on: another call
        # began after they began and before they were done.
        spans = [one for one in models.spans if one[1] is not None]
        self.assertTrue(any(start < other < end for start, end in spans
                            for other in models.other_starts), (spans, models.other_starts))
        # One at a time, in order: each began after the one before was done.
        for before, after in zip(spans, spans[1:]):
            self.assertGreaterEqual(after[0], before[1])
        # Drawn from a copy of the version's own files - gone once drawn.
        self.assertTrue(models.copies and all(models.copies), models.copies)
        self.assertIn("notes.md", models.copies[0])
        slides_root = Path(said["folder"]) / "runs" / said["run"] / "slides"
        self.assertFalse((slides_root / ".work").exists()
                         and any((slides_root / ".work").iterdir()))
        # Each call recorded under its own part, though the two ran at once:
        # the slides' as the slides, the others' not.
        from test_work_transcript import HEADER_BLOCK
        session = next((Path(said["folder"]) / "run_logs").glob(f"*_{said['run']}"))
        headers = HEADER_BLOCK.findall((session / "transcript-001.txt").read_text(encoding="utf-8"))
        slides_part = f"slides of {rounds[0]['id']}"
        self.assertTrue([one for one in headers if one[2].strip() == slides_part
                         and one[1] == "BUILDER"])
        self.assertFalse([one for one in headers if one[2].strip() == slides_part
                          and one[1] != "BUILDER"], "the reviewer's call is not the slides'")


class _RunsTheWork(_Draws):
    """The builder drawing a version's slides runs the work's own code, as
    an app that makes its folder as it starts does: in the copy of the
    version it is drawn from - and once in the work's folder too."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ran = []

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        if ("MAKE THE PROGRESS SLIDES FOR VERSION" in first and "IN WORDS" not in first
                and len(request.messages) == 1 and not self.ran):
            copy = re.search(r"a copy of its files is in (.+?)\. Read", first).group(1)
            work = re.search(r"the work's folder,\s+(.+?), is already", first).group(1)
            self.ran.append((copy, work))
            code = ("import os\n"
                    f"os.makedirs(os.path.join({copy!r}, 'instance'), exist_ok=True)\n"
                    f"open(os.path.join({copy!r}, 'instance', 'app.db'), 'w').write('x')\n"
                    "print('COPY WRITTEN')\n"
                    "try:\n"
                    f"    os.makedirs(os.path.join({work!r}, 'instance'))\n"
                    "    print('WORK WRITTEN')\n"
                    "except Exception as exc:\n"
                    "    print('WORK REFUSED', type(exc).__name__)\n")
            self.asked.append(request)
            return self._counted(self._reply("", [
                ToolCall(id="w1", name="run_python", arguments={"code": code})]))
        if self.ran and len(self.ran) == 1 and "MAKE THE PROGRESS SLIDES FOR VERSION" in first \
                and len(request.messages) > 1 and not hasattr(self, "said"):
            self.said = json.dumps(request.messages[-1]["content"])
        return super().invoke(request, role)


class TheWorksOwnCodeRunsInTheCopyTests(_Base):
    """What the work's own code writes as it runs, when the slides run it to
    show what it does, is written in the copy of the version they are drawn
    from - thrown away with it - and never in the work's folder."""

    def test_written_in_the_copy_not_in_the_work(self) -> None:
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        models = _RunsTheWork(outside=str(outside / "none.png"))
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        self.assertTrue(models.ran)
        self.assertIn("COPY WRITTEN", models.said)
        self.assertIn("WORK REFUSED", models.said)
        self.assertNotIn("WORK WRITTEN", models.said)
        self.assertFalse((Path(said["folder"]) / "instance").exists())
        self.assertFalse(Path(models.ran[0][0]).exists(), "the copy thrown away")


class _ReadsTheVersionBefore(_Draws):
    """The builder drawing a version's slides compares it with the version
    before: reads that version's copy, and tries to write in it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.before = []

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        found = re.search(r"the version before: a copy of its files is\s+in (.+?) - READ", first)
        if (found and "MAKE THE PROGRESS SLIDES FOR VERSION" in first
                and len(request.messages) == 1 and not self.before):
            before = found.group(1)
            self.before.append(before)
            code = ("import os\n"
                    f"names = [f for _, _, fs in os.walk({before!r}) for f in fs]\n"
                    "print('BEFORE HAS', len(names), sorted(names)[:5])\n"
                    "try:\n"
                    f"    open(os.path.join({before!r}, 'changed.txt'), 'w').write('x')\n"
                    "    print('BEFORE WRITTEN')\n"
                    "except Exception as exc:\n"
                    "    print('BEFORE REFUSED', type(exc).__name__)\n")
            self.asked.append(request)
            return self._counted(self._reply("", [
                ToolCall(id="b1", name="run_python", arguments={"code": code})]))
        if (self.before and len(request.messages) > 1 and not hasattr(self, "said")
                and "MAKE THE PROGRESS SLIDES FOR VERSION" in first):
            self.said = json.dumps(request.messages[-1]["content"])
        return super().invoke(request, role)


class TheVersionBeforeIsReadNotChangedTests(_Base):
    """A version's slides compare it with the version before: that one's
    files are copied beside, named where they are, to read - never changed -
    and thrown away with the rest."""

    def test_read_not_written_and_gone_after(self) -> None:
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        models = _ReadsTheVersionBefore(outside=str(outside / "none.png"))
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        self.assertTrue(models.before, "the version before was named to read")
        self.assertIn("BEFORE HAS", models.said)
        self.assertIn("BEFORE REFUSED", models.said)
        self.assertNotIn("BEFORE WRITTEN", models.said)
        self.assertFalse(Path(models.before[0]).exists(), "the copy thrown away")
        # Told its round, and the one name its title slide gives it.
        firsts = [one.messages[0]["content"] for one in models.asked]
        asked = next(one if isinstance(one, str) else json.dumps(one) for one in firsts
                     if "the version before: a copy of its files" in
                     (one if isinstance(one, str) else json.dumps(one)))
        self.assertRegex(asked, r'It is ROUND (\d+) of the work: the title slide says '
                                r'"v\d+ - round \1"')


class SlidesLeftUndrawnTests(_Base):
    """The computer went off while a version's slides were drawn: the run is
    interrupted, the version saved without them. Continued, they are drawn
    first - from that version's own files - and added to it."""

    def test_drawn_when_it_is_continued(self) -> None:
        from personalized_agent.agent.work.runner import INTERRUPTED, WorkRuns
        from personalized_agent.agent.work.versions import VersionStore
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        models = _Draws(outside=str(outside / "none.png"))
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        store = VersionStore(Path(said["folder"]) / "runs" / said["run"])
        store.set_slides("v2", None, {"slides": "drawing"})
        self.app.db.execute("UPDATE work_runs SET status = ? WHERE id = ?",
                            (INTERRUPTED, said["run"]))
        # Not going: shown as never drawn.
        self.assertEqual(self.app.work.version(said["run"], "v2")["slides"],
                         {"status": "not made", "why": "the run ended before they were drawn"})
        drawn = len(models.drawn)
        again = WorkRuns(self.app.db, self.app.policy, models,
                         self.app.state_heartbeat.experience, platform=self.app.platform)
        # Not the final checks' test (test_final_checks), as support.py's.
        again.final_checks = False
        self.app.work = again
        continued = again.continue_run(said["run"], self.unit,
                                       {"calls": 0, "minutes": 0, "tokens": 0})
        self.assertNotIn("error", continued, continued)
        for _ in range(600):
            if not again.runs(self.unit["id"])[0]["alive"]:
                break
            __import__("time").sleep(0.1)
        self.assertEqual(again.version(said["run"], "v2")["slides"]["status"], "made")
        self.assertIn("v2", "".join(models.drawn[drawn:]))
        log = [one.get("what") for one in again.runs(self.unit["id"])[0]["log"]]
        self.assertIn("slides drawn again", log)


class TheSlidesAreDrawnTests(_Base):
    """Each version's slides DRAWN by the builder with its tools - figures in
    the version's own slides folder, which is the only place it writes
    then - and laid out as 16:9 slides from a fixed set of layouts; kept
    with the version, out of its files."""

    def setUp(self) -> None:
        super().setUp()
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        self.outside = outside / "outside.png"
        self.outside.write_bytes(b"\x89PNG\r\n\x1a\nnot really")

    def test_drawn_kept_and_shown(self) -> None:
        models = _Draws(outside=str(self.outside))
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        folder = Path(said["folder"])
        run_dir = folder / "runs" / said["run"]
        tree = self.app.work.versions(said["run"])
        versions = tree["versions"]
        self.assertEqual({one["summary"]["slides"] for one in versions[1:]}, {"made"})
        self.assertEqual(len(models.drawn), len(versions) - 1, "none for the contract")
        self.assertEqual(models.worded, [], "nothing fell back to words")
        # Its code ran in the slides folder, and could write nowhere else.
        self.assertTrue(all("REFUSED" in one and "ESCAPED" not in one for one in models.results))
        self.assertFalse((folder / "escaped.txt").exists())
        self.assertFalse((folder / "stray.md").exists())
        self.assertTrue(all("unknown tool web_search" in one for one in models.results))
        # The figures are in each version's own slides folder ...
        head = self.app.work.version(said["run"], tree["head"])
        n = tree["head"]
        self.assertTrue((run_dir / "slides" / n / "fig_progress.png").is_file())
        self.assertTrue((run_dir / "slides" / n / "fig_flow.svg").is_file())
        # ... which is no part of the work's files.
        for one in versions:
            files = self.app.work.version(said["run"], one["id"])["files"]
            self.assertFalse([f for f in files if "/slides/" in f["path"]], files)
        # The page: 16:9 slides, the figures carried in it, the diagram an
        # SVG, the numbers big, the timeline, and the controls.
        slides = head["slides"]
        self.assertEqual((slides["status"], slides["format"]), ("made", "drawn"))
        page = slides["page"]
        self.assertEqual(page.count('<section class="slide">'), slides["count"])
        self.assertEqual(slides["count"], 7)
        for shown in ("data:image/png;base64,", "data:image/svg+xml;base64,", '<svg class="dg"',
                      'class="mx-value"', 'class="tl"', 'class="frame"', "1280", 'id="prev"',
                      'id="next"', 'id="where"', 'id="full"', "requestFullscreen",
                      f"The notes, drawn {len(versions) - 1}"):
            self.assertIn(shown, page)
        # Beside each slide, what it shows and how to read it - and a slide
        # given none says so.
        self.assertIn('class="deck with-notes"', page)
        self.assertEqual(page.count('class="note"'), slides["count"])
        for said_beside in ("How many lines notes.md has, round by round", "Key terms",
                            "the file the work writes", "How to read it", "It grew each round",
                            "No explanation was written for this slide."):
            self.assertIn(said_beside, page)
        # An image outside the work's folder, or missing, is left out - said so.
        self.assertNotIn("outside.png", page)
        self.assertNotIn("nowhere.png", page)
        store = __import__("personalized_agent.agent.work.versions", fromlist=["x"]).VersionStore(
            run_dir)
        state = store.get_json(store.manifest(n)["state"])
        deck = state["slides"]
        self.assertEqual(set(deck["figures"]), {f"runs/{said['run']}/slides/{n}/fig_progress.png",
                                                f"runs/{said['run']}/slides/{n}/fig_flow.svg"})
        self.assertTrue(any("outside.png" in one and "not inside" in one for one in deck["dropped"]))
        self.assertTrue(any("nowhere.png" in one and "not found" in one for one in deck["dropped"]))
        # Kept with the version: gone from the disk, still shown.
        import shutil
        shutil.rmtree(run_dir / "slides")
        again = self.app.work.version(said["run"], "v2")["slides"]["page"]
        self.assertIn("data:image/png;base64,", again)
        self.assertIn("The notes, drawn 1", again)
        # Each round of its tools counted, and in the transcript as the
        # slides of the version.
        total = row["plan"]["total"]
        self.assertEqual(total["calls"], len(models.asked))
        self.assertEqual(total["tokens_out"], 7 * len(models.asked))
        from test_work_transcript import HEADER_BLOCK
        session = next((folder / "run_logs").glob(f"*_{said['run']}"))
        headers = HEADER_BLOCK.findall((session / "transcript-001.txt").read_text(encoding="utf-8"))
        self.assertEqual(len(headers), len(models.asked))
        self.assertEqual([one[1:3] for one in headers if one[2].strip() == "slides of v2"],
                         [("BUILDER", "slides of v2")] * 2, "two calls: drawing, then the spec")
        # The builder's prompt: what this version did, its folder, the layouts.
        self.assertIn("WHAT THIS VERSION DID", models.drawn[1])
        self.assertIn("THE LAYOUTS", models.drawn[0])
        self.assertIn(str(run_dir / "slides" / "v2"), models.drawn[1])

    def test_figures_that_cannot_be_shown_fall_back_to_words(self) -> None:
        models = _Draws(outside=str(self.outside), bad=True)
        said, row = self._done(models)
        self.assertEqual(row["status"], "done", row["log"][-6:])
        versions = self.app.work.versions(said["run"])["versions"]
        self.assertEqual({one["summary"]["slides"] for one in versions[1:]}, {"made"})
        self.assertEqual(len(models.worded), len(versions) - 1, "none for the contract")
        self.assertIn("no figure could be shown", models.worded[0])
        slides = self.app.work.version(said["run"], "v2")["slides"]
        self.assertEqual(slides["format"], "words")
        self.assertEqual(slides["fallback"], "no figure could be shown")
        self.assertIn("Text slides - the figures could not be made", slides["page"])
        self.assertIn("In words 1", slides["page"])
        self.assertEqual(row["plan"]["total"]["calls"], len(models.asked))


class EachSlidesNotesTests(unittest.TestCase):
    """A slide's notes, as the builder may give them: the four parts, plain
    words, or terms as "term: meaning" lines - bounded, and none dropped
    silently into nothing."""

    def test_the_shapes_notes_come_in(self) -> None:
        from personalized_agent.agent.work import deck
        whole = deck._notes_of({"shows": "x" * 2000, "read": "axes",
                                "terms": [{"term": "acc", "means": "accuracy"},
                                          "F1: the balance of precision and recall",
                                          "no colon here", {"term": "lonely"}] * 3})
        self.assertLessEqual(len(whole["shows"]), 700)
        self.assertEqual(whole["terms"][:2], [
            {"term": "acc", "means": "accuracy"},
            {"term": "F1", "means": "the balance of precision and recall"}])
        self.assertLessEqual(len(whole["terms"]), deck.MOST_TERMS)
        self.assertEqual(deck._notes_of("just words")["shows"], "just words")
        self.assertEqual(deck._notes_of(None), {})
        self.assertEqual(deck._notes_of({"shows": "", "terms": []}), {})
        # Escaped where shown.
        self.assertIn("&lt;b&gt;", deck.notes_html({"shows": "<b>bold</b>"}))

    def test_a_deck_with_no_notes_shows_none_beside_it(self) -> None:
        from personalized_agent.agent.work import deck
        spec = deck.visual_spec({"title": "T", "slides": [
            {"layout": "metrics", "title": "N", "metrics": [{"value": "1", "label": "x"}]}]})
        page = deck.render(spec)
        self.assertNotIn("with-notes", page)
        self.assertNotIn('class="note"', page)


class TheSlidesReadASealedWorkTests(unittest.TestCase):
    """The slides' code reads the work it draws - also where the work's
    folder is SEALED, as an Experience debugger way's is from the other two:
    what it inherits from a wider readable root (the home) stops at it, and
    the slides were refused every file of the work."""

    def test_the_work_folder_is_named_and_read(self) -> None:
        import shutil
        from personalized_agent.agent.work.runner import Run
        from personalized_agent.runtime import isolation
        from personalized_agent.runtime.permissions import WorkspacePolicy
        from personalized_agent.runtime.sandbox import SandboxRuntime
        root = Path(tempfile.mkdtemp(prefix="pa-sealed-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        work = root / "project" / "B"
        work.mkdir(parents=True)
        (work / "notes.md").write_text("the work's own words", encoding="utf-8")
        policy = WorkspacePolicy(workspace_root=root / "workspace", writable_root=work,
                                 readable_paths=[root], network=False)
        isolation.seal_from_containers([work])
        runner = SimpleNamespace(
            runtime=SandboxRuntime(policy, sandbox_enabled=True), folder=work,
            tools=SimpleNamespace(locked=[]), slides_root=None, packages_dir=None,
            _tool_used=lambda *a, **k: None, _changed=lambda *a, **k: None,
            _install=lambda *a, **k: None)
        slides = work / "runs" / "run_x" / "slides" / "v1"
        tools = Run._slides_tools(runner, slides)
        self.assertIn(work, [Path(one).resolve() for one in
                             tools.runtime.policy.readable_paths])
        said = tools.call("run_python", {"code": (
            f"print(open({str(work / 'notes.md')!r}, encoding='utf-8').read())")})
        self.assertIn("the work's own words", said.get("stdout", ""), said)


class TheSingleModelDrawsNoneTests(unittest.TestCase):
    """The Experience debugger's single model makes no slides - drawn or in
    words - and its turns' versions are named by the commit it wrote."""

    def test_no_slides_and_turns_named_by_their_commits(self) -> None:
        from test_experience_debugger import _DebuggerBase, _Models

        class _Commits(_Models):
            def invoke(self, request, role=""):
                reply = super().invoke(request, role)
                if "on your own" in (request.system or "") and reply.text:
                    said = json.loads(reply.text)
                    said["commit"] = {"title": f"Add alone.md, turn {self.alone_answers}",
                                      "body": ["alone.md: made alone"]}
                    reply.text = reply.raw_content = json.dumps(said)
                return reply

        class _Case(_DebuggerBase):
            def runTest(self):
                pass

        case = _Case()
        case.setUp()
        try:
            models = _Commits(alone_turns=2)
            case.app.work.models = models
            project = case._project()
            case._start(project, "single", instruction="write the short notes")
            row = case._finished(project, "single")
            versions = case.app.work.versions(row["id"])["versions"]
            self.assertTrue(versions)
            self.assertEqual({one["summary"]["slides"] for one in versions}, {"none"})
            self.assertFalse(any("MAKE THE PROGRESS SLIDES" in json.dumps(one.messages)
                                 for one in models.asked))
            self.assertFalse((Path(row["folder"]) / "runs" / row["id"] / "slides").exists())
            turn = [one for one in versions if one["kind"] == "turn"][0]
            self.assertEqual(turn["message"]["title"], "Add alone.md, turn 1")
            self.assertEqual((turn["round"], turn["message"]["body"]),
                             (1, ["alone.md: made alone"]))
        finally:
            case.tearDown()


class OldDecksStillRenderTests(unittest.TestCase):

    def test_text_slides_and_results_decks_in_the_new_frame(self) -> None:
        from personalized_agent.agent.work.deck import render
        old = {"kind": "progress", "version": "v2", "title": "Where the notes stand",
               "summary": "The notes exist.", "done": [{"what": "notes", "evidence": "notes.md"}],
               "results": [{"title": "The greeting", "figure": "C:/x/fig.png"}],
               "next": ["a title"]}
        page = render(old, lambda path: "/served/" + path)
        self.assertEqual(page.count('<section class="slide">'), 4)
        for shown in ("Done so far", "/served/C:/x/fig.png", 'class="s-text"', 'id="full"'):
            self.assertIn(shown, page)
        results = render({"title": "Results", "deliverables": [{"name": "n", "location": "x"}]})
        self.assertIn("Key deliverables", results)
        self.assertIn('class="frame"', results)

    def test_the_spec_is_made_safe(self) -> None:
        from personalized_agent.agent.work.deck import place_images, visual_spec
        spec = visual_spec({"title": "t", "slides": [
            {"layout": "kpi", "metrics": [{"value": str(n), "label": "x"} for n in range(9)]},
            {"layout": "image-text", "image": "bad.png", "points": ["a", "b"]},
            {"layout": "chart", "image": "bad.png"},
            {"layout": "nonsense"},
            {"layout": "bullets", "points": [f"p{n}" for n in range(9)]},
            {"layout": "diagram", "nodes": ["a", "b"], "edges": [{"from": "a", "to": "zz"}]}]})
        slides = place_images(spec, lambda path: "")
        self.assertEqual([one["layout"] for one in slides],
                         ["metrics", "bullets", "bullets", "diagram"])
        self.assertEqual(len(slides[0]["metrics"]), 4)
        self.assertEqual(len(slides[2]["points"]), 5)
        self.assertEqual(slides[3]["edges"], [])


class TheVersionOpensAsSlidesTests(unittest.TestCase):
    """Opened in the tree, a version shows its slides - prev, next, a
    counter, the arrow keys - and no dump of its record; its frame exactly
    as tall as the slide filling its width, and the bar beneath it."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_slides_not_json(self) -> None:
        from personalized_agent.agent.work.deck import render
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        deck = {"kind": "progress", "version": "v2", "label": "Round 1", "since": "v1",
                "title": "Where the notes stand", "summary": "The notes exist.",
                "goal": "notes.md says hello",
                "done": [{"what": "notes.md written", "evidence": "notes.md line 1"}],
                "new": ["notes.md"], "remaining": ["a title"], "problems": [],
                "next": ["write the title"]}
        slides = render(deck, lambda path: path)
        count = slides.count('<section class="slide">')
        run = {"id": "run_2", "folder": "C:/w/agent_work/x", "alive": False,
               "status": "done", "continuable": False, "extendable": True,
               "started_at": "2026-10-08T12:00:00", "instruction": "write the notes",
               "final": {}, "log": [], "plan": {"packages": {"mode": "ask", "requests": []}},
               "versions": {"count": 2, "head": "v2", "branch": "main"}}
        summary = {"rounds": 1, "cards": 1, "cards_done": 1, "todo": 0, "deck": False,
                   "phase": "rounds", "status": "running", "slides": "made",
                   "tokens_in": 1200000, "tokens_out": 85000}
        tree = {"run": "run_2", "head": "v2", "branch": "main", "branches": ["main"],
                "alive": False, "status": "done",
                "versions": [{"id": "v1", "n": 1, "parent": "", "branch": "main",
                              "kind": "contract", "label": "The contract approved",
                              "at": "2026-10-08T12:01:00", "files": 1, "granted": 0,
                              "summary": dict(summary, slides="not made")},
                             {"id": "v2", "n": 2, "parent": "v1", "branch": "main",
                              "kind": "round", "label": "Round 1", "at": "2026-10-08T12:02:00",
                              "files": 2, "granted": 0, "summary": summary}]}
        detail = {"id": "v2", "label": "Round 1", "kind": "round", "branch": "main",
                  "parent": "v1", "at": "2026-10-08T12:02:00", "head": True,
                  "goal": "notes.md says hello", "phase": "rounds", "summary": summary,
                  "files": [{"path": "notes.md", "size": 5}], "granted": [],
                  "todo": {"next": ["write the title"]}, "deck": False,
                  "contract": {"goal": "notes.md says hello", "scope": [], "out_of_scope": [],
                               "instructions": [], "deliverables": []},
                  "slides": {"status": "made", "count": count, "page": slides}}
        stub = {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens": 1000000,
                                                  "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/versions": tree, "/api/work/version": detail}
        steps = (
            "activeTab='experience';"
            "document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.id==='experience'));"
            "experienceState.unit='task-ver';await xpLoadRuns();"
            "document.querySelector('[data-xp-versions=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "out.v1=document.querySelector('[data-xp-version-row=\"v1\"]').textContent.replace(/\\s+/g,' ');"
            "out.v2=document.querySelector('[data-xp-version-row=\"v2\"]').textContent.replace(/\\s+/g,' ');"
            "document.querySelector('[data-xp-version-view=\"v2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(600);"
            "const view=document.querySelector('.xp-version-view');"
            "const frame=view.querySelector('iframe[data-xp-version-slides]');"
            "out.frame=!!frame;"
            "const doc=frame.contentDocument;"
            "out.slides=doc.querySelectorAll('section.slide').length;"
            "const on=()=>[...doc.querySelectorAll('section.slide')].findIndex(s=>s.classList.contains('on'));"
            "out.first=on();out.where=doc.getElementById('where').textContent;"
            "doc.getElementById('next').click();out.afterNext=on();"
            "out.where2=doc.getElementById('where').textContent;"
            "doc.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight'}));out.afterKey=on();"
            "doc.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft'}));out.afterLeft=on();"
            "out.cover=doc.querySelector('section.slide h1').textContent;"
            "out.pres=view.querySelectorAll('pre').length;"
            "const shown=[...view.childNodes].filter(n=>!(n.tagName==='DETAILS'&&!n.open))"
            "  .map(n=>n.textContent||'').join(' ');"
            "out.braces=/[{}]/.test(shown)||/\"status\"|\"page\"/.test(shown);"
            "out.view=view.textContent.replace(/\\s+/g,' ');"
            # As tall as the slide filling its width, and the bar: nothing
            # above or below the slide to scroll past.
            "await wait(800);"
            "const stage=doc.getElementById('stage');"
            "const notes=doc.getElementById('notes');"
            "out.fit={frame:frame.clientHeight,stage:stage.clientHeight,"
            "  wide:stage.clientWidth,bar:doc.querySelector('.bar').offsetHeight,"
            "  window:window.innerHeight,notes:notes?notes.offsetHeight:0,"
            "  beside:notes?notes.getBoundingClientRect().left>=stage.getBoundingClientRect().right-1:false,"
            "  stickies:doc.querySelectorAll('.note.on details.sticky').length,"
            "  scrolls:[doc.scrollingElement,notes].filter(Boolean)"
            "    .some(el=>el.scrollHeight>el.clientHeight+1)};")
        harness = (
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>window.__posts=[];window.__gets=[];const STUB=" + json.dumps(stub).replace("</", "<" + chr(92) + "/") + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
            "else window.__gets.push(String(u));"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||80));"
            "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--window-size=1200,900", "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertIn("slides not made", out["v1"])
        self.assertIn("· slides", out["v2"])
        self.assertIn("1.2M in · 85k out so far", out["v2"])
        self.assertTrue(out["frame"])
        self.assertEqual(out["slides"], count)
        self.assertGreaterEqual(count, 4)
        self.assertEqual(out["first"], 0)
        self.assertEqual(out["where"], f"1 / {count}")
        self.assertEqual(out["afterNext"], 1)
        self.assertEqual(out["where2"], f"2 / {count}")
        self.assertEqual(out["afterKey"], 2)
        self.assertEqual(out["afterLeft"], 1)
        self.assertEqual(out["cover"], "Where the notes stand")
        self.assertEqual(out["pres"], 0, "no dump of the version's record")
        self.assertFalse(out["braces"], out["view"])
        fit = out["fit"]
        self.assertGreater(fit["wide"], 400, fit)
        # The slide fills the stage's width, and the stage is its height.
        self.assertLessEqual(abs(fit["stage"] - fit["wide"] * 720 / 1280), 2, fit)
        self.assertLess(fit["stage"], fit["window"], fit)
        # The notes BESIDE the slide, as sticky notes - and the frame as
        # tall as the slide or the notes, whichever is taller, and the bar:
        # nothing in it scrolls.
        if fit["notes"]:
            self.assertTrue(fit["beside"], fit)
            self.assertGreaterEqual(fit["stickies"], 1, fit)
        self.assertLessEqual(abs(fit["frame"] - max(fit["stage"], fit["notes"]) - fit["bar"]), 2, fit)
        self.assertFalse(fit["scrolls"], fit)


if __name__ == "__main__":
    unittest.main()

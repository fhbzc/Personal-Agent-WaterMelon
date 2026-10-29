"""LINES FOUND BY A WORD, AND READ ON FROM THERE (the user, 2026-10-29): a
function looked for by its name was a file read whole from its first line,
or a script written to search. find_in_files gives each line that holds a
word or a pattern with its file and number - never inside installed
packages, saved versions or binary files - and read_file reads on from a
line, counted from 1, with where to go on from when it was cut. A builder
call that hands over says what each file it read holds and at which lines,
so the fresh call reads only those."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.work import runner as R
from personalized_agent.agent.work import tools as T
from personalized_agent.agent.work.parallel import READ_ONLY

SCRIPT = "\n".join(
    ["import json", "", ""]
    + [f"# filler line {n}" for n in range(40)]
    + ["def estimate_slope(rows):", "    total = sum(rows)", "    return total / len(rows)",
       "", "", "def main():", "    print(estimate_slope([1, 2, 3]))", ""])


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        experience = self.app.state_heartbeat.experience
        made = experience.store.create_work_project("Finding lines")
        self.unit = experience.unit(made["unit_id"])
        self.folder = self.app.work.folder_of(self.unit)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.tools = T.WorkTools(self.app.work._runtime(self.unit, self.folder))
        (self.folder / "src").mkdir()
        (self.folder / "src" / "model.py").write_text(SCRIPT, encoding="utf-8")
        (self.folder / "notes.md").write_text("The slope is estimated in model.py.\n",
                                              encoding="utf-8")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def find(self, **args):
        return self.tools.call("find_in_files", {"root": str(self.folder), **args})


class FindingLinesTests(_Base):

    def test_a_function_by_its_name_with_its_line(self) -> None:
        said = self.find(pattern="def estimate_slope", literal=True)
        self.assertEqual(said["lines_found"], 1, said)
        self.assertEqual(said["files"], [{"path": str(self.folder / "src" / "model.py"),
                                          "lines": ["44: def estimate_slope(rows):"],
                                          "blocks": [{"line": 44, "ends_about": 46,
                                                      "by": "Python's parser"}]}])
        # And read on from just before it.
        read = self.tools.call("read_file", {"path": said["files"][0]["path"], "from_line": 44})
        self.assertTrue(read["text"].startswith("def estimate_slope(rows):"), read["text"][:40])
        self.assertEqual(read["from_line"], 44)

    def test_a_pattern_the_files_asked_for_and_the_lines_around(self) -> None:
        said = self.find(pattern=r"estimate_\w+\(", files="*.py", context=1)
        self.assertEqual([one["path"] for one in said["files"]],
                         [str(self.folder / "src" / "model.py")])
        self.assertEqual(said["files"][0]["lines"],
                         ["43- # filler line 39", "44: def estimate_slope(rows):",
                          "45-     total = sum(rows)", "--", "49- def main():",
                          "50:     print(estimate_slope([1, 2, 3]))"])
        # Any case, and a whole path's pattern.
        said = self.find(pattern="SLOPE", ignore_case=True, files="**/*.md")
        self.assertEqual([one["path"] for one in said["files"]], [str(self.folder / "notes.md")])

    def test_one_file_and_what_it_says_when_nothing_holds_it(self) -> None:
        said = self.tools.call("find_in_files", {"root": str(self.folder / "notes.md"),
                                                 "pattern": "slope"})
        self.assertEqual(said["files"][0]["lines"], ["1: The slope is estimated in model.py."])
        said = self.find(pattern="no such words anywhere")
        self.assertEqual(said["files"], [])
        self.assertIn("no line holds it", said["note"])

    def test_a_bad_pattern_is_said_with_the_way_out(self) -> None:
        said = self.find(pattern="estimate_slope(")
        self.assertIn("literal=true", said["error"])
        said = self.find(pattern="estimate_slope(", literal=True)
        self.assertEqual(said["lines_found"], 2)

    def test_at_most_so_many_lines_and_it_says_there_are_more(self) -> None:
        said = self.find(pattern="filler", most=5)
        self.assertEqual(said["lines_found"], 5)
        self.assertIn("more than 5", said["cut"])

    def test_never_packages_versions_environments_or_binary_files(self) -> None:
        for where in (".packages/lib", ".versions/v1", "venv1/Lib/site-packages/x", "tryout"):
            (self.folder / where).mkdir(parents=True, exist_ok=True)
            (self.folder / where / "copy.py").write_text("def estimate_slope(): pass\n",
                                                         encoding="utf-8")
        (self.folder / "tryout" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
        (self.folder / "blob.bin").write_bytes(b"\0\1def estimate_slope")
        said = self.find(pattern="def estimate_slope")
        self.assertEqual([one["path"] for one in said["files"]],
                         [str(self.folder / "src" / "model.py")])
        self.assertEqual(said["not_searched"], {"binary": 1})

    def test_the_runs_own_folder_and_scratch_only_when_asked_for(self) -> None:
        checks = self.folder / "runs" / "run_0123456789abcdef" / "checks"
        scratch = self.folder / ".agent_runtime" / "tmp"
        for where in (checks, scratch):
            where.mkdir(parents=True)
            (where / "out.txt").write_text("estimate_slope gave 2.0\n", encoding="utf-8")
        (self.folder / "runs" / "try_one").mkdir()
        (self.folder / "runs" / "try_one" / "copy.py").write_text(
            "estimate_slope = None\n", encoding="utf-8")
        said = self.find(pattern="estimate_slope", files="*.txt")
        self.assertEqual(said["files"], [])
        # A folder of its own tries under runs/ is the work's: searched.
        said = self.find(pattern="estimate_slope = None")
        self.assertEqual([one["path"] for one in said["files"]],
                         [str(self.folder / "runs" / "try_one" / "copy.py")])
        for where in (checks, scratch):
            said = self.tools.call("find_in_files", {"root": str(where), "pattern": "gave"})
            self.assertEqual([one["path"] for one in said["files"]], [str(where / "out.txt")])

    def test_only_reads_and_runs_beside_the_other_reading_tools(self) -> None:
        self.assertIn("find_in_files", READ_ONLY)
        self.assertIn("find_in_files", [one.name for one in self.tools.specs(writing=False)])
        self.assertIn("find_in_files", R.SLIDES_TOOLS)
        self.assertEqual(R._short_result("find_in_files", self.find(pattern="slope"))[0],
                         "3 lines in 2 files of 2")


class ReadingOnTests(_Base):

    def test_lines_count_from_one_and_a_cut_read_says_where_to_go_on(self) -> None:
        spec = next(one for one in self.tools.specs() if one.name == "read_file")
        self.assertNotIn("counting from 0", spec.description)
        self.assertIn("lines count from 1", spec.description)
        long = "\n".join(f"line {n:05d} " + "x" * 90 for n in range(1, 1001))
        (self.folder / "long.txt").write_text(long, encoding="utf-8")
        first = self.tools.call("read_file", {"path": str(self.folder / "long.txt")})
        self.assertTrue(first["cut"])
        on = first["next_from_line"]
        # The line the cut fell in, read again whole - none left out.
        self.assertIn(f"line {on:05d} ", first["text"].splitlines()[-1])
        then = self.tools.call("read_file", {"path": str(self.folder / "long.txt"),
                                             "from_line": on})
        self.assertTrue(then["text"].startswith(f"line {on:05d} "))
        self.assertNotIn("next_from_line", self.tools.call(
            "read_file", {"path": str(self.folder / "notes.md")}))

    def test_found_then_read_only_that_part(self) -> None:
        spec = next(one for one in self.tools.specs() if one.name == "read_file")
        self.assertIn("to_line", spec.input_schema["properties"])
        found = self.find(pattern="def estimate_slope", literal=True)
        path = found["files"][0]["path"]
        said = self.tools.call("read_file", {"path": path, "from_line": 44, "to_line": 46})
        self.assertEqual(said["text"].splitlines(),
                         ["def estimate_slope(rows):", "    total = sum(rows)",
                          "    return total / len(rows)"])
        self.assertEqual((said["from_line"], said["to_line"], said["lines"]), (44, 46, 50))
        self.assertFalse(said["cut"])
        # Past the end: to the end; one line: that line.
        said = self.tools.call("read_file", {"path": path, "from_line": 49, "to_line": 900})
        self.assertEqual((said["to_line"], said["text"].splitlines()[0]), (50, "def main():"))
        said = self.tools.call("read_file", {"path": path, "from_line": 44, "to_line": 44})
        self.assertEqual(said["text"], "def estimate_slope(rows):")

    def test_an_end_before_the_start_is_said(self) -> None:
        said = self.tools.call("read_file", {"path": str(self.folder / "src" / "model.py"),
                                             "from_line": 44, "to_line": 10})
        self.assertIn("before from_line 44", said["error"])

    def test_a_document_reads_to_its_end_line_too(self) -> None:
        from unittest import mock
        from personalized_agent.runtime import documents
        text = "\n".join(f"page line {n}" for n in range(1, 21))
        with mock.patch.object(documents, "extract",
                               return_value={"kind": "pdf", "text": text, "pages": [1]}):
            said = documents.read_for_model("x.pdf", 5, 40000, to_line=7)
        self.assertEqual(said["text"].splitlines(),
                         ["page line 5", "page line 6", "page line 7"])
        self.assertEqual(said["to_line"], 7)


class WhereABlockEndsTests(_Base):
    """Each line found that opens a block, with where it likely ends - said
    to be an estimate, the agent's to change (blocks.py)."""

    def blocks(self, name, text, pattern):
        (self.folder / name).write_text(text, encoding="utf-8")
        said = self.tools.call("find_in_files", {"root": str(self.folder / name),
                                                 "pattern": pattern})
        return (said["files"][0].get("blocks") if said["files"] else None), said

    def test_said_to_be_an_estimate_the_agent_may_change(self) -> None:
        spec = next(one for one in self.tools.specs() if one.name == "find_in_files")
        self.assertIn("ESTIMATE - yours to change", spec.description)
        self.assertIn("to_line=ends_about", spec.description)

    def test_python_by_its_parser_decorators_and_classes_too(self) -> None:
        text = ("import functools\n\n\n@functools.cache\ndef cached(x):\n    if x:\n"
                "        return 1\n    return 2\n\n\nclass Model:\n    def fit(self):\n"
                "        return self\n\n    def score(self):\n        return 0\n")
        found, _ = self.blocks("m.py", text, r"^@functools|class Model|def score")
        self.assertEqual(found, [
            {"line": 4, "ends_about": 8, "by": "Python's parser"},
            {"line": 11, "ends_about": 16, "by": "Python's parser"},
            {"line": 15, "ends_about": 16, "by": "Python's parser"}])
        # And then only that part is read.
        said = self.tools.call("read_file", {"path": str(self.folder / "m.py"),
                                             "from_line": 15, "to_line": 16})
        self.assertEqual(said["text"], "    def score(self):\n        return 0")

    def test_python_that_does_not_parse_by_its_indentation(self) -> None:
        text = "def broken(:\n    a = 1\n\n    b = 2\nprint(3)\n"
        found, _ = self.blocks("b.py", text, "def broken")
        self.assertEqual(found, [{"line": 1, "ends_about": 4, "by": "indentation"}])

    def test_r_by_matching_braces_past_strings_and_comments(self) -> None:
        text = ('estimate <- function(rows)\n{\n  label <- "a } in a string"\n'
                "  # a } in a comment\n  if (length(rows) > 0) {\n    sum(rows)\n  }\n}\n"
                "other <- 1\n")
        found, _ = self.blocks("est.R", text, "estimate <- function")
        self.assertEqual(found, [{"line": 1, "ends_about": 8, "by": "matching braces"}])

    def test_javascript_and_stata_braces(self) -> None:
        found, _ = self.blocks("a.js", "function f() {\n  /* } */\n  return '}';\n}\nf();\n",
                               "function f")
        self.assertEqual(found, [{"line": 1, "ends_about": 4, "by": "matching braces"}])
        found, _ = self.blocks("run.do", "foreach v in a b {\n  display \"`v'\"\n}\n",
                               "foreach")
        self.assertEqual(found, [{"line": 1, "ends_about": 3, "by": "matching braces"}])

    def test_markdown_up_to_the_next_heading_as_high(self) -> None:
        text = ("# Package\n\n## Data\n\nWhere it comes from.\n\n```\n# not a heading\n```\n\n"
                "### Sources\n\nA list.\n\n## Code\n\nScripts.\n")
        found, _ = self.blocks("README.md", text, "^## Data")
        self.assertEqual(found, [{"line": 3, "ends_about": 13, "by": "the next heading as high"}])

    def test_a_line_that_opens_nothing_has_no_end(self) -> None:
        found, said = self.blocks("plain.py", "x = 1\ny = estimate(x)\n", "estimate")
        self.assertIsNone(found)
        self.assertEqual(said["files"][0]["lines"], ["2: y = estimate(x)"])
        found, _ = self.blocks("notes.txt", "def f():\n    pass\n", "def f")
        self.assertIsNone(found, "a language it does not know: no end")


class FindingFilesLeavesTheSameOutTests(_Base):
    """find_files finds what a glob finds - never into installed packages,
    saved versions, the agent's scratch or the run's own folder from above
    (the user, 2026-10-29: 5 to 97 seconds a call through 70,000 files)."""

    def setUp(self) -> None:
        super().setUp()
        for where in (".packages/lib", ".versions/v1", "venv1/Lib/site-packages/x", "tryout",
                      ".agent_runtime/tmp", "runs/run_0123456789abcdef/checks", "runs/try_one"):
            (self.folder / where).mkdir(parents=True, exist_ok=True)
            (self.folder / where / "copy.py").write_text("x = 1\n", encoding="utf-8")
        (self.folder / "tryout" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")

    def files(self, pattern, root=None, **more):
        said = self.tools.call("find_files", {"root": str(root or self.folder),
                                              "pattern": pattern, **more})
        return sorted(Path(one).relative_to(root or self.folder).as_posix()
                      for one in said["files"])

    def test_all_the_way_down_but_not_into_what_is_left_out(self) -> None:
        self.assertEqual(self.files("**/*.py"), ["runs/try_one/copy.py", "src/model.py"])
        self.assertEqual(self.files("**/*.PY"), ["runs/try_one/copy.py", "src/model.py"])

    def test_a_pattern_means_what_a_glob_means(self) -> None:
        (self.folder / "top.py").write_text("y = 2\n", encoding="utf-8")
        self.assertEqual(self.files("*.py"), ["top.py"], "the top folder only")
        self.assertEqual(self.files("src/*.py"), ["src/model.py"])
        self.assertEqual(self.files("*"), ["notes.md", "top.py"])
        self.assertEqual(self.files("**/model.?y"), ["src/model.py"])

    def test_inside_what_is_left_out_when_asked_for(self) -> None:
        checks = self.folder / "runs" / "run_0123456789abcdef" / "checks"
        self.assertEqual(self.files("**/*.py", root=checks), ["copy.py"])
        venv = self.folder / "tryout"
        self.assertEqual(self.files("*.py", root=venv), ["copy.py"])

    def test_containing_never_reads_a_file_too_big(self) -> None:
        with mock.patch.object(T, "MOST_SEARCHED_BYTES", 10):
            said = self.tools.call("find_files", {"root": str(self.folder), "pattern": "**/*.py",
                                                  "containing": "estimate_slope"})
        self.assertEqual(said["files"], [])
        self.assertIn("were not looked into", said["not_searched"])
        said = self.tools.call("find_files", {"root": str(self.folder), "pattern": "**/*.py",
                                              "containing": "estimate_slope"})
        self.assertEqual(said["files"], [str(self.folder / "src" / "model.py")])


class TheHandoverMapsWhatWasReadTests(unittest.TestCase):

    def test_the_note_asks_for_each_file_read_and_its_lines(self) -> None:
        asked = R.HAND_OVER.format(steps=40, minutes=12)
        self.assertIn('"read":', asked)
        self.assertIn("WHERE - the lines of each part", asked)
        told = R.HANDED_OVER.format(calls=2, note="{}", files="a.py")
        self.assertIn("find_in_files", told)
        self.assertIn("read only the part you need", told)


if __name__ == "__main__":
    unittest.main()

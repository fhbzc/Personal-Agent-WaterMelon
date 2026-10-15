#!/usr/bin/env python3
"""Run the test suite, or part of it, in a few processes at once.

    python scripts/run_tests.py                  # everything, 4 processes
    python scripts/run_tests.py scheduler        # one file
    python scripts/run_tests.py models panels    # a few files
    python scripts/run_tests.py -k relocat       # tests whose name matches
    python scripts/run_tests.py --serial         # one process, as unittest runs it

THE WORK IS DIVIDED BY HOW LONG IT TAKES, not by its name. A few classes
take minutes on their own - a sandbox run, a long grouping round, a page
drawn in a browser forty times - and most take a second. Dealt out by name,
two of the long ones land in one process and it runs them one after the
other while the others sit idle; that process is the whole run's wait.

So: each class's time, as last measured, is kept in tests/.durations.json
(this machine's; not in git). A class expected to take long is run test by
test, so its tests spread over every process. What is long runs alone,
longest first; what is short goes in batches of about BATCH_SECONDS. Each
process takes the next item as it comes free - so none waits while another
works through a pile. Every batch measures its own tests, and the times are
kept for the next run. Never measured: the file's tests counted as a few
seconds each.

Each process shares nothing with the others, so a failure in one says
nothing about the rest. Every failure is printed in full at the end, and
the exit code is non-zero if any failed. A test that fails only when the
machine is busy passes when run alone: run it alone before believing it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS = REPO_ROOT / "tests"
DURATIONS = TESTS / ".durations.json"
TIMED_RUNNER = Path(__file__).resolve().parent / "timed_unittest.py"

#: A class expected to take longer than this is run test by test.
SPLIT_SECONDS = 90.0
#: Short items are put together until a batch is about this long: each
#: process pays the imports once per batch.
BATCH_SECONDS = 45.0
#: Never measured: what one test is counted as.
UNMEASURED_TEST_SECONDS = 2.0


def environment() -> dict:
    # Tests must never reach the real keychain, whichever way they are run, and
    # each worker imports the test helpers from the tests directory itself.
    existing = os.environ.get("PYTHONPATH", "")
    path = os.pathsep.join([str(TESTS), str(REPO_ROOT)] +
                           ([existing] if existing else []))
    return dict(os.environ, PERSONALIZED_AGENT_NO_KEYRING="1", PYTHONPATH=path)


def test_files(patterns: List[str]) -> List[Path]:
    files = sorted(TESTS.glob("test_*.py"))
    if not patterns:
        return files
    lowered = [p.lower() for p in patterns]
    return [f for f in files if any(p in f.name.lower() for p in lowered)]


def test_classes(files: List[Path]) -> Dict[str, List[str]]:
    """Every test class, as a dotted name unittest can be handed, with the
    names of its tests - read from the source rather than imported.

    A test class is one made from TestCase, directly or through a class of
    the same file (`class LiveTests(_PanelBase)`): counting only the first
    left whole files' worth of tests unrun. A base with no tests of its own
    whose name begins with "_" is not run on its own."""
    out: Dict[str, List[str]] = {}
    for path in files:
        module = path.stem
        bases: Dict[str, List[str]] = {}
        tests: Dict[str, List[str]] = {}
        current = None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            found = re.match(r"class (\w+)\(([^)]*)\)", line)
            if found:
                current = found.group(1)
                bases[current] = [b.strip().split(".")[-1] for b in found.group(2).split(",")]
                tests[current] = []
            elif line.startswith(("class ", "def ")) or (line and not line[0].isspace()
                                                         and not line.startswith(("#", "@"))):
                current = None if not line.startswith("@") else current
            elif current and re.match(r"    def (test\w*)\(", line):
                tests[current].append(re.match(r"    def (test\w*)\(", line).group(1))

        def is_test(name: str, seen=()) -> bool:
            if name in seen:
                return False
            return any(base == "TestCase" or (base in bases and is_test(base, seen + (name,)))
                       for base in bases.get(name, []))
        for name in bases:
            if is_test(name) and not (name.startswith("_") and not tests[name]):
                out[f"{module}.{name}"] = tests[name]
    return out


def measured() -> Dict[str, float]:
    try:
        return {k: float(v) for k, v in json.loads(DURATIONS.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def keep(times: Dict[str, float]) -> None:
    """The times just measured, over what was kept - a test's latest, and a
    class's as the sum of its tests'."""
    known = measured()
    for name, seconds in times.items():
        known[name] = round(seconds, 2)
    classes: Dict[str, float] = {}
    for name, seconds in known.items():
        if name.count(".") == 2:                       # module.Class.test
            cls = name.rsplit(".", 1)[0]
            classes[cls] = classes.get(cls, 0.0) + seconds
    known.update({k: round(v, 2) for k, v in classes.items()})
    try:
        DURATIONS.write_text(json.dumps(dict(sorted(known.items())), indent=0) + "\n",
                             encoding="utf-8")
    except OSError:
        pass


def items(classes: Dict[str, List[str]]) -> List[Tuple[float, List[str]]]:
    """What the processes are handed: (expected seconds, names) - a long class
    test by test, the rest in batches; longest first."""
    known = measured()
    units: List[Tuple[float, str]] = []
    for cls, tests in classes.items():
        guess = known.get(cls, UNMEASURED_TEST_SECONDS * max(1, len(tests)))
        if guess > SPLIT_SECONDS and len(tests) > 1:
            for test in tests:
                name = f"{cls}.{test}"
                units.append((known.get(name, guess / len(tests)), name))
        else:
            units.append((guess, cls))
    units.sort(key=lambda one: -one[0])
    out: List[Tuple[float, List[str]]] = []
    batch: List[str] = []
    batch_seconds = 0.0
    for seconds, name in units:
        if seconds >= BATCH_SECONDS:
            out.append((seconds, [name]))
            continue
        if batch and batch_seconds + seconds > BATCH_SECONDS:
            out.append((batch_seconds, batch))
            batch, batch_seconds = [], 0.0
        batch.append(name)
        batch_seconds += seconds
    if batch:
        out.append((batch_seconds, batch))
    out.sort(key=lambda one: -one[0])
    return out


def run_item(names: List[str], name_filter: Optional[str]) -> Tuple[int, str, float, int, Dict[str, float]]:
    with tempfile.TemporaryDirectory(prefix="pa-times-") as work:
        times_file = Path(work) / "times.json"
        command = [sys.executable, str(TIMED_RUNNER), str(times_file), *names]
        if name_filter:
            command += ["-k", name_filter]
        started = time.time()
        completed = subprocess.run(command, capture_output=True, text=True,
                                   cwd=str(REPO_ROOT), env=environment())
        try:
            said = json.loads(times_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            said = {}
    return (completed.returncode, completed.stderr + completed.stdout,
            time.time() - started, int(said.get("ran", 0)), said.get("times", {}))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the test suite, or part of it, in a few processes at once")
    parser.add_argument("patterns", nargs="*",
                        help="only files whose name contains one of these")
    parser.add_argument("-k", dest="name_filter", default=None,
                        help="only tests whose name matches this")
    parser.add_argument("--serial", action="store_true",
                        help="run in one process, the way unittest does")
    parser.add_argument("--jobs", type=int, default=4,
                        help="how many at once (default 4; measured best on this "
                             "machine - more processes only compete for the same disk)")
    args = parser.parse_args(argv)

    files = test_files(args.patterns)
    if not files:
        print(f"no test files match {args.patterns}", file=sys.stderr)
        return 2

    if args.serial:
        command = [sys.executable, "-m", "unittest", "discover", "-s", str(TESTS)]
        if args.name_filter:
            command += ["-k", args.name_filter]
        return subprocess.run(command, cwd=str(REPO_ROOT),
                              env=environment()).returncode

    classes = test_classes(files)
    if not classes:
        print("no test classes found", file=sys.stderr)
        return 2
    work = items(classes)
    jobs = max(1, min(args.jobs, len(work)))
    expected = sum(seconds for seconds, _names in work)
    print(f"running {len(classes)} test classes from {len(files)} files as "
          f"{len(work)} items in {jobs} processes "
          f"(about {expected / 60 / jobs:.0f} min of work each)\n", flush=True)

    started = time.time()
    failures: List[str] = []
    times: Dict[str, float] = {}
    total = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        # Longest first: each process takes the next as it comes free.
        futures = [(names, pool.submit(run_item, names, args.name_filter))
                   for _seconds, names in work]
        for names, future in futures:
            code, output, seconds, count, measured_times = future.result()
            total += count
            times.update(measured_times)
            if count == 0 and args.name_filter:
                continue
            mark = "ok  " if code == 0 else "FAIL"
            label = names[0] if len(names) == 1 else f"{names[0]} and {len(names) - 1} more"
            print(f"  {mark} {seconds:6.1f}s  {count:3d} tests  {label}", flush=True)
            if code != 0:
                failures.append(output)
    keep(times)

    elapsed = time.time() - started
    print()
    for output in failures:
        marker = output.find("=" * 20)
        print(output[marker:].strip() if marker >= 0 else output.strip())
        print()
    verdict = "OK" if not failures else f"FAILED ({len(failures)} item(s))"
    print(f"{total} tests in {elapsed / 60:.1f} min: {verdict}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

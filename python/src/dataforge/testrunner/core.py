"""The core of testrunner, which every task's copy has.

It holds, in this order: the globs of a manifest, the manifest, the CTRF
report, what a runner's adapter is given and gives back, each runner's data,
the checks of a fix's files, restoring a project's files, reading a run
group's report, and the run.

The globs of a manifest's restore and outputs, matched on project paths.

A glob is a path relative to the project's root, `/`-separated. `*`, `?` and
`[...]` match within one component, never across a `/`, and `*` matches a
leading `.` too; a component that is `**` matches any number of components,
none among them. So `spec/**` matches `spec` and everything under it, and
`**/notes.txt` a notes.txt anywhere. A glob never names a `.git`, so
that a manifest's globs never delete the project's own repository.

The manifest of a task's tests, tests/manifest.toml.

  [[run]]      a run group: name, and optionally dir, the directory under
               the project it starts in (default "."); and either preset,
               one of RUNNERS, or run, an argv, parse, a format of
               FORMATS or an argv, and optionally outputs, globs of
               the reports the framework writes inside the project
  [restore]    paths, globs of the project's files checked back to the base
               before the tests run (restore)
  [[test]]     suite, id, and either run, the name of its run group, or
               runner, one of RUNNERS; and optionally dir, with runner
               only, the directory under the project the runner starts in
               (default "."), kind, principle and expect, "failed" for its
               group's canary
  [[file]]     source, a file under tests/, and dest, where under the
               project it is copied before the tests run
  [no_tests]   <suite> = why the suite has no test

Only the structure is checked here: the types, that each test's (suite, id),
each run group's name and each file's dest appear once, that a test's id
appears once in a group that runs its own command, that the paths and globs
stay inside their directories, that a suite of no_tests has no test, and,
when the caller gives them, that each principle is one of the task's and
that no dest is a file of the project's base. The values a kind or a suite
may take, and which suite may have which kind, are the dataset's to check.

The tests' outcomes as a CTRF report, written to /logs/verifier/ctrf.json.

CTRF (https://ctrf.io/docs/specification/test, 0.1.0) is the format Harbor's
viewer reads at that path. Each test object is one case a runner reported,
with its suite as `suite`, a one-element array, and in `labels` the
reference of the manifest's test it belongs to (`id`), its runner or run
group when it has them, its kind and principle when it has them, and
`expect` for a canary. CTRF has no status for an error: one is `failed` with
`rawStatus` `error`. A test the manifest lists that the runner did not
report is such an error, so that a test kept from running does not pass
unseen. A canary, which must fail on an assertion, counts in no suite; the
report's `results.extra` lists in `canary_broken` each canary that did not,
and in `unlisted` the names a run group reported that the manifest does not
list; with neither, the report has no `extra`.

What every runner's adapter is given and gives back.

Each runner's adapter and data, from its own module once it is asked for.

registry.MODULES names the module of each runner, whose SPECS gives the
runner's Spec. A task's copy may hold the modules of only the runners its
manifest names (bundle.files), so a runner's module is imported only when
its adapter or data is asked for. The check of the build files that say
where dependencies come from is no runner's (deps).

A runner is started once for each suite and directory, on the tests of the
manifest that name it there, and writes its report outside the project. A
file that only the runner loads by itself (runner_files) is for the dataset
to check back to the project's base before the tests run. A build file that
also holds the runner's configuration, which a fix may need to change, is
not: its configuration is taken from the base and given to the runner on the
command line, where the runner allows it, and a test the runner then leaves
out is an error all the same (missing); where no command line replaces
it (build_config), the dataset keeps it at the base. A project file that the
base's configuration names to run in the test process, such as a plugin or a
setup file, is neither: it is the dataset's to restore.

The checks a dataset makes of a fix's files, beside deps'.

is_test_file and is_test_dir tell a test's files and directories by their
paths alone, whatever the language, for a dataset to keep a fix from
changing them. A language's module may add to them, and give checks of its
own, by optional hooks of the same names: PLAIN_SOURCES, the suffixes of
its source files, which a test's directory or name does not make a test's;
kept_lines, the lines of the base's files that hold listed tests and that a
fix must leave as they are; and rewires, what lines a fix adds that change
what the tests check with no change to them. registry.GUARDS names these
modules; a task's copy has those its runners use or whose language is in
its project (bundle.guards_for), and its GUARDS names only those, so that
each check gathers the hooks of the modules the copy has.

Checking a project's files back to its base, and clearing a run's reports.

restore writes each file of the base that a glob matches from the base's
git objects, and deletes each file that a glob matches, the base lacks and
git does not ignore. It runs no `git checkout`: a filter a `.gitattributes`
names, or a hook, which the tests' side may have written, would run there.
Each git command runs with only the repository's own configuration, with
its hooks and fsmonitor off and replace refs not read.

The formats of a run group's report that testrunner reads itself.

`junit` is every `*.xml` file under the report's directory, JUnit XML, as
junit.report_cases reads it. `ctrf` is every `*.json` file, CTRF, of which
only each test's `name`, one line, `status`,
one of CTRF's, and `rawStatus` are read: only `passed` passes, `pending` is
read as `skipped` and `other` as `failed`, and `rawStatus` `error` makes a
test an error, so that it never makes a test pass; `message`, `trace`,
`duration` and `filePath` are kept when they have the right type. A file
over LIMIT bytes, not UTF-8, or not of its format's shape makes the whole
report unreadable.

Running the tests of a manifest on a project.
"""

from __future__ import annotations

from collections.abc import (
  Callable,
  Collection,
  Iterable,
  Iterator,
  Mapping,
  Sequence,
)
from dataclasses import dataclass, field
import fnmatch
import importlib
import itertools
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from types import ModuleType
from typing import cast, Protocol

from .registry import GUARDS, MODULES

ANY = "**"
"""The component that matches any number of components."""

GIT = ".git"
"""The name a glob never names, and a path a glob matches never holds."""

_MAGIC = re.compile(r"[*?[]")


def valid(value: object) -> str | None:
  """Return a glob that stays inside the project, or None.

  Args:
    value: The glob.

  Returns:
    It, or None when it is not a string, is absolute, or has an empty, `.`,
    `..` or `.git` component, or one that holds `**` but is not `**`.
  """
  if not isinstance(value, str):
    return None
  for part in value.split("/"):
    if part in ("", ".", "..", GIT) or "\\" in part or "\0" in part:
      return None
    if ANY in part and part != ANY:
      return None
  return value


def matches(path: str, globs: Iterable[str]) -> bool:
  """Return whether a path matches one of some globs.

  Args:
    path: A path relative to the project, `/`-separated.
    globs: The globs.

  Returns:
    Whether one of them matches the whole path.
  """
  parts = path.split("/")
  return any(_match(g.split("/"), parts) for g in globs)


def _match(pattern: list[str], parts: list[str]) -> bool:
  if not pattern:
    return not parts
  if pattern[0] == ANY:
    return any(_match(pattern[1:], parts[n:]) for n in range(len(parts) + 1))
  return (
    bool(parts)
    and fnmatch.fnmatchcase(parts[0], pattern[0])
    and _match(pattern[1:], parts[1:])
  )


def escape(path: str) -> str:
  """Return the glob that matches a path and nothing else.

  Args:
    path: A path relative to the project.

  Returns:
    The path with each `*`, `?` and `[` in brackets.
  """
  return _MAGIC.sub(lambda m: f"[{m.group()}]", path)


def literal_prefix(glob: str) -> list[str]:
  """Return the components a glob starts with that hold no wildcard.

  Args:
    glob: The glob.

  Returns:
    Its leading components up to the first with `*`, `?` or `[`.
  """
  found: list[str] = []
  for part in glob.split("/"):
    if _MAGIC.search(part):
      break
    found.append(part)
  return found


NAME = "manifest.toml"
"""The manifest's name in a task's tests/."""

RUNNERS = tuple(MODULES)
"""The runners a test may name."""

SUITE = re.compile(r"[a-z][a-z0-9_]*")
"""What a suite's name matches, as a whole; it holds no `::`."""

FORMATS = ("junit", "ctrf")
"""The formats of a run group's report that testrunner reads itself."""

EXPECT_FAILED = "failed"
"""The one value of a test's expect, which makes it its group's canary."""

_TEST_KEYS = {
  "suite",
  "runner",
  "run",
  "id",
  "dir",
  "kind",
  "principle",
  "expect",
}
_RUN_KEYS = {"name", "dir", "preset", "run", "parse", "outputs"}
_FILE_KEYS = {"source", "dest"}
_TOP_KEYS = {"test", "run", "restore", "file", "no_tests"}


class ManifestError(ValueError):
  """A manifest that fails a check, with every problem found."""

  def __init__(self, problems: list[str]) -> None:
    """Make the error.

    Args:
      problems: One line for each problem.
    """
    super().__init__("; ".join(problems))
    self.problems: list[str] = problems


@dataclass(frozen=True, slots=True)
class Test:
  """A test the manifest lists.

  Attributes:
    suite: The suite it counts in, such as p2p.
    runner: The runner that runs it, one of RUNNERS, or None when its run
      group runs a command of its own.
    id: Its ID in the runner's own form, paths relative to the project.
    dir: The directory, relative to the project, the runner starts in.
    kind: What it checks, in the dataset's words, or None.
    principle: The ID of the principle it checks, or None.
    run: The name of its run group, or None when it names its runner.
    expect: EXPECT_FAILED for its group's canary, which must fail on an
      assertion and counts in no suite; else None.
  """

  suite: str
  runner: str | None
  id: str
  dir: str = "."
  kind: str | None = None
  principle: str | None = None
  run: str | None = None
  expect: str | None = None

  @property
  def ref(self) -> str:
    """The reference to it, `<suite>::<id>`, unique in a manifest."""
    return f"{self.suite}::{self.id}"


@dataclass(frozen=True, slots=True)
class Run:
  """A run group: how its tests are run and how their report is read.

  Attributes:
    name: Its name, which its tests' run gives.
    dir: The directory, relative to the project, it starts in.
    preset: The runner it is, one of RUNNERS; or None when it runs a
      command of its own.
    run: That command's argv, None with a preset.
    parse: One of FORMATS, read from TESTRUNNER_OUT; or an argv that writes
      CTRF to TESTRUNNER_CTRF; None with a preset.
    outputs: Globs of the reports the command writes inside the project,
      deleted before it runs.
  """

  name: str
  dir: str = "."
  preset: str | None = None
  run: tuple[str, ...] | None = None
  parse: str | tuple[str, ...] | None = None
  outputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class File:
  """A file the manifest copies into the project before the tests run.

  Attributes:
    source: Its path relative to tests/.
    dest: Its path relative to the project.
  """

  source: str
  dest: str


@dataclass(frozen=True, slots=True)
class Manifest:
  """A manifest that passed every check.

  Attributes:
    tests: The tests, in the file's order.
    files: The files to copy, in the file's order.
    no_tests: Why each suite named here has no test.
    runs: The run groups, in the file's order.
    restore: The globs of [restore]'s paths.
  """

  tests: tuple[Test, ...]
  files: tuple[File, ...]
  no_tests: Mapping[str, str]
  runs: tuple[Run, ...] = ()
  restore: tuple[str, ...] = ()

  @property
  def runners(self) -> frozenset[str]:
    """The runners its tests and its run groups' presets name.

    A run group with a command of its own names none.
    """
    return frozenset(
      {t.runner for t in self.tests if t.runner}
      | {r.preset for r in self.runs if r.preset}
    )

  @property
  def formats(self) -> frozenset[str]:
    """The formats of FORMATS its run groups' parse names.

    A parse argv names none, nor does a preset.
    """
    return frozenset(r.parse for r in self.runs if isinstance(r.parse, str))


def read(
  tests: Path,
  *,
  principles: Collection[str] | None = None,
  base_files: Collection[str] | None = None,
) -> Manifest:
  """Read and check the manifest in a task's tests/.

  Args:
    tests: The task's tests/ directory.
    principles: The IDs of the task's principles, which a test's principle
      must be one of; None checks nothing of them.
    base_files: The paths, relative to the project, of the files of its
      base, which no dest may be; None checks nothing of them.

  Returns:
    The manifest.
  """
  text = (tests / NAME).read_text(encoding="utf-8")
  return load(text, principles=principles, base_files=base_files)


def load(
  text: str,
  *,
  principles: Collection[str] | None = None,
  base_files: Collection[str] | None = None,
) -> Manifest:
  """Check a manifest's text.

  Args:
    text: The manifest's TOML.
    principles: As read takes it.
    base_files: As read takes it.

  Returns:
    The manifest.

  Raises:
    ManifestError: When a check fails.
  """
  try:
    data: dict[str, object] = tomllib.loads(text)
  except tomllib.TOMLDecodeError as e:
    raise ManifestError([f"not TOML: {e}"]) from e
  problems = [f"unknown key {k!r}" for k in data if k not in _TOP_KEYS]
  runs = _runs(data.get("run", []), problems)
  restore = _restore(data.get("restore", {}), problems)
  tests = _tests(data.get("test", []), runs, principles, problems)
  files = _files(data.get("file", []), base_files, problems)
  no_tests = _no_tests(data.get("no_tests", {}), tests, problems)
  if problems:
    raise ManifestError(problems)
  return Manifest(
    tuple(tests), tuple(files), no_tests, tuple(runs.values()), restore
  )


def relative(value: object, *, dot: bool = False) -> str | None:
  """Return a path that stays inside its directory, or None.

  Args:
    value: The path, `/`-separated and relative.
    dot: Whether "." itself, the directory, is taken.

  Returns:
    The path, or None when it is not a string, is absolute, is empty or
    has an empty, `.` or `..` component.
  """
  if not isinstance(value, str):
    return None
  if dot and value == ".":
    return value
  parts = value.split("/")
  if any(p in ("", ".", "..") or "\\" in p or "\0" in p for p in parts):
    return None
  return value


def _tables(
  value: object, key: str, problems: list[str]
) -> list[dict[str, object]]:
  items = cast("list[object]", value) if isinstance(value, list) else None
  if items is None or not all(isinstance(t, dict) for t in items):
    problems.append(f"{key} is not an array of tables")
    return []
  return cast("list[dict[str, object]]", items)


def _string(
  table: dict[str, object], key: str, where: str, problems: list[str]
) -> str:
  value = table.get(key)
  if not isinstance(value, str) or not value.strip() or "\n" in value:
    problems.append(f"{where}: {key} is not a non-empty line of text")
    return ""
  return value


def _tests(
  value: object,
  runs: Mapping[str, Run],
  principles: Collection[str] | None,
  problems: list[str],
) -> list[Test]:
  tests: list[Test] = []
  seen: set[tuple[str, str]] = set()
  in_run: set[tuple[str, str]] = set()
  for n, table in enumerate(_tables(value, "test", problems), 1):
    where = f"test {n}"
    problems += [
      f"{where}: unknown key {k!r}" for k in table if k not in _TEST_KEYS
    ]
    suite = _string(table, "suite", where, problems)
    if suite and not SUITE.fullmatch(suite):
      problems.append(f"{where}: suite {suite!r} is not a plain name")
    id_ = _string(table, "id", where, problems)
    where = f"test {n} ({suite}::{id_})"
    if (suite, id_) in seen:
      problems.append(f"{where}: listed twice")
    seen.add((suite, id_))
    group, runner, dir_ = _runner(table, runs, id_, where, in_run, problems)
    kind = _optional(table, "kind", where, problems)
    principle = _optional(table, "principle", where, problems)
    if principle and principles is not None and principle not in principles:
      problems.append(f"{where}: no principle {principle!r}")
    expect = _optional(table, "expect", where, problems)
    if expect is not None and expect != EXPECT_FAILED:
      problems.append(f"{where}: expect is not {EXPECT_FAILED!r}")
    tests.append(
      Test(
        suite,
        runner,
        id_,
        dir_ or ".",
        kind,
        principle,
        group.name if group is not None else None,
        expect,
      )
    )
  return tests


def _runner(
  table: dict[str, object],
  runs: Mapping[str, Run],
  id_: str,
  where: str,
  in_run: set[tuple[str, str]],
  problems: list[str],
) -> tuple[Run | None, str | None, str | None]:
  # A test's run group, runner and dir.
  if "run" not in table:
    runner = _string(table, "runner", where, problems)
    if runner and runner not in RUNNERS:
      problems.append(f"{where}: unknown runner {runner!r}")
    dir_ = relative(table.get("dir", "."), dot=True)
    if dir_ is None:
      problems.append(f"{where}: dir is not a path inside the project")
    return None, runner, dir_
  name = _string(table, "run", where, problems)
  group = runs.get(name)
  if "runner" in table or "dir" in table:
    problems.append(f"{where}: runner and dir are its run's")
  if group is None:
    if name:
      problems.append(f"{where}: no run {name!r}")
    return None, None, "."
  if group.preset is None:
    if (group.name, id_) in in_run:
      problems.append(f"{where}: its id is listed twice in run {name!r}")
    in_run.add((group.name, id_))
  return group, group.preset, group.dir


def _runs(value: object, problems: list[str]) -> dict[str, Run]:
  runs: dict[str, Run] = {}
  for n, table in enumerate(_tables(value, "run", problems), 1):
    where = f"run {n}"
    problems += [
      f"{where}: unknown key {k!r}" for k in table if k not in _RUN_KEYS
    ]
    name = _string(table, "name", where, problems)
    if name and not SUITE.fullmatch(name):
      problems.append(f"{where}: name {name!r} is not a plain name")
    where = f"run {n} ({name})"
    if name in runs:
      problems.append(f"{where}: named twice")
    dir_ = relative(table.get("dir", "."), dot=True)
    if dir_ is None:
      problems.append(f"{where}: dir is not a path inside the project")
    if "preset" in table:
      preset = _string(table, "preset", where, problems)
      if preset and preset not in RUNNERS:
        problems.append(f"{where}: unknown preset {preset!r}")
      if any(k in table for k in ("run", "parse", "outputs")):
        problems.append(f"{where}: a preset has no run, parse or outputs")
      group = Run(name, dir_ or ".", preset=preset)
    else:
      argv = _argv(table.get("run"), f"{where}: run", problems)
      parse = table.get("parse")
      if not (isinstance(parse, str) and parse in FORMATS):
        parse = _argv(parse, f"{where}: parse, if not a format,", problems)
      outputs = _globs(table.get("outputs", []), f"{where}: outputs", problems)
      group = Run(name, dir_ or ".", None, argv, parse, outputs)
    if name not in runs:
      runs[name] = group
  return runs


def _argv(value: object, where: str, problems: list[str]) -> tuple[str, ...]:
  items = cast("list[object]", value) if isinstance(value, list) else []
  if not items or not all(
    isinstance(a, str) and a and "\0" not in a for a in items
  ):
    problems.append(f"{where} is not a non-empty array of non-empty strings")
    return ()
  return tuple(cast("list[str]", items))


def _globs(value: object, where: str, problems: list[str]) -> tuple[str, ...]:
  items = cast("list[object]", value) if isinstance(value, list) else None
  if items is None:
    problems.append(f"{where} is not an array")
    return ()
  found: list[str] = []
  for item in items:
    glob = valid(item)
    if glob is None:
      problems.append(f"{where}: {item!r} is not a glob inside the project")
    else:
      found.append(glob)
  return tuple(found)


def _restore(value: object, problems: list[str]) -> tuple[str, ...]:
  if not isinstance(value, dict):
    problems.append("restore is not a table")
    return ()
  table = cast("dict[str, object]", value)
  problems += [f"restore: unknown key {k!r}" for k in table if k != "paths"]
  return _globs(table.get("paths", []), "restore: paths", problems)


def _optional(
  table: dict[str, object], key: str, where: str, problems: list[str]
) -> str | None:
  return _string(table, key, where, problems) if key in table else None


def _files(
  value: object, base_files: Collection[str] | None, problems: list[str]
) -> list[File]:
  files: list[File] = []
  dests: set[str] = set()
  for n, table in enumerate(_tables(value, "file", problems), 1):
    where = f"file {n}"
    problems += [
      f"{where}: unknown key {k!r}" for k in table if k not in _FILE_KEYS
    ]
    source = relative(table.get("source"))
    if source is None:
      problems.append(f"{where}: source is not a path inside tests/")
    dest = relative(table.get("dest"))
    if dest is None:
      problems.append(f"{where}: dest is not a path inside the project")
    elif dest in dests:
      problems.append(f"{where}: dest {dest!r} is listed twice")
    elif base_files is not None and dest in base_files:
      problems.append(f"{where}: dest {dest!r} is a file of the base")
    if dest is not None:
      dests.add(dest)
    files.append(File(source or "", dest or ""))
  return files


def _no_tests(
  value: object, tests: list[Test], problems: list[str]
) -> dict[str, str]:
  if not isinstance(value, dict):
    problems.append("no_tests is not a table")
    return {}
  suites = {t.suite for t in tests}
  found: dict[str, str] = {}
  for suite, why in cast("dict[str, object]", value).items():
    if not SUITE.fullmatch(suite):
      problems.append(f"no_tests: suite {suite!r} is not a plain name")
    if not isinstance(why, str) or not why.strip():
      problems.append(f"no_tests: {suite} gives no reason")
    elif suite in suites:
      problems.append(f"no_tests: {suite} has tests")
    else:
      found[suite] = why
  return found


FILE = "ctrf.json"
"""The report's name in the verifier's output directory."""

SPEC_VERSION = "0.1.0"
"""The CTRF version the report follows."""

TOOL = "testrunner"
"""The report's tool."""

ERROR = "error"
"""The rawStatus of a test that errored."""

MISSING = "listed in the manifest but not in the runner's report"
"""The message of a test the runner did not report."""

STATUSES = ("passed", "failed", "skipped")
"""The statuses a case may have."""

EXPECT = "expect"
"""The label of a canary, whose value is the manifest's expect."""

MESSAGE_LIMIT = 2000
TRACE_LIMIT = 20000
"""How many characters of a message and a trace the report keeps."""

TestObject = dict[str, object]
"""A CTRF test object, as JSON."""


@dataclass(frozen=True, slots=True)
class Case:
  """One test case as a runner reported it.

  Attributes:
    name: Its ID, in the runner's form, paths relative to the project.
    status: One of STATUSES.
    duration: How long it ran, in milliseconds.
    error: Whether it errored rather than failed; its status is failed.
    message: Its failure's or skip's message, or None.
    trace: Its failure's trace, or None.
    file: The file that defines it, relative to the project, or None.
  """

  name: str
  status: str
  duration: int = 0
  error: bool = False
  message: str | None = None
  trace: str | None = None
  file: str | None = None


def labels(test: Test) -> dict[str, str]:
  """Return the labels of a test's cases.

  Args:
    test: The manifest's test.

  Returns:
    Its reference, and its runner, run group, kind, principle and expect
    when it has them.
  """
  found = {"id": test.ref}
  if test.runner is not None:
    found["runner"] = test.runner
  if test.run is not None:
    found["run"] = test.run
  if test.kind is not None:
    found["kind"] = test.kind
  if test.principle is not None:
    found["principle"] = test.principle
  if test.expect is not None:
    found[EXPECT] = test.expect
  return found


def entry(case: Case, suite: str, tags: Mapping[str, str]) -> TestObject:
  """Return the CTRF test object of a case.

  Args:
    case: The case.
    suite: The suite it counts in.
    tags: Its labels.

  Returns:
    The test object.
  """
  return plain(case, suite=[suite]) | {"labels": dict(tags)}


def plain(case: Case, **extra: object) -> TestObject:
  """Return the CTRF test object of a case, with no suite or labels.

  Args:
    case: The case.
    **extra: Keys that come after its duration, such as its suite.

  Returns:
    The test object.
  """
  found: TestObject = {
    "name": case.name,
    "status": case.status,
    "duration": case.duration,
    **extra,
  }
  if case.error:
    found["rawStatus"] = ERROR
  if case.message:
    found["message"] = case.message[:MESSAGE_LIMIT]
  if case.trace:
    found["trace"] = case.trace[:TRACE_LIMIT]
  if case.file:
    found["filePath"] = case.file
  return found


def missing(test: Test, why: str = "") -> TestObject:
  """Return the CTRF test object of a test the runner did not report.

  Args:
    test: The manifest's test.
    why: What else is known, such as how the runner exited; or "".

  Returns:
    The test object, an error.
  """
  message = f"{MISSING} ({why})" if why else MISSING
  case = Case(test.id, "failed", error=True, message=message)
  return entry(case, test.suite, labels(test))


def report(
  tests: list[TestObject],
  start: int,
  stop: int,
  *,
  unlisted: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
  """Return the CTRF report of test objects.

  Args:
    tests: The test objects.
    start: When the run started, in milliseconds since the epoch.
    stop: When it stopped, likewise.
    unlisted: For each run group that reported names the manifest does not
      list, `{run, total, names}`, as Results gives it.

  Returns:
    The report; its results.extra, when there is one, has canary_broken,
    the references of the canaries with a test object that is not a
    failure of an assertion, and unlisted.
  """
  summary = {
    s: sum(t["status"] == s for t in tests)
    for s in ("passed", "failed", "pending", "skipped", "other")
  }
  results: dict[str, object] = {
    "tool": {"name": TOOL},
    "summary": {"tests": len(tests), **summary, "start": start, "stop": stop},
    "tests": tests,
  }
  extra: dict[str, object] = {}
  broken = canary_broken(tests)
  if broken:
    extra["canary_broken"] = broken
  if unlisted:
    extra["unlisted"] = [dict(u) for u in unlisted]
  if extra:
    results["extra"] = extra
  return {
    "reportFormat": "CTRF",
    "specVersion": SPEC_VERSION,
    "results": results,
  }


def canary_broken(tests: list[TestObject]) -> list[str]:
  """Return the canaries that did not fail on an assertion.

  Args:
    tests: The test objects.

  Returns:
    The reference of each canary with a test object that is not `failed`,
    or is an error, in the order of their first.
  """
  good: dict[str, bool] = {}
  for test in tests:
    tags = _labels(test)
    if tags.get(EXPECT) == EXPECT_FAILED:
      ref = str(tags.get("id"))
      failed = test.get("status") == "failed"
      good[ref] = (
        good.get(ref, True) and failed and test.get("rawStatus") != ERROR
      )
  return [ref for ref, ok in good.items() if not ok]


def _labels(test: Mapping[str, object]) -> dict[str, object]:
  tags = test.get("labels")
  return cast("dict[str, object]", tags) if isinstance(tags, dict) else {}


def suite_counts(report: Mapping[str, object]) -> dict[str, tuple[int, int]]:
  """Return how many tests of each suite passed, and how many there are.

  Args:
    report: A CTRF report, as report gives it or as JSON read back.

  Returns:
    For each suite that has a test, (passed, total); a skipped test does
    not pass, and a canary counts in none.

  Raises:
    ValueError: When the report has no results.tests list, or a test has no
      one-element suite or no status.
  """
  results = report.get("results")
  tests = (
    cast("dict[str, object]", results).get("tests")
    if isinstance(results, dict)
    else None
  )
  if not isinstance(tests, list):
    raise ValueError("the report has no results.tests")
  counts: dict[str, tuple[int, int]] = {}
  for test in cast("list[object]", tests):
    found = cast("dict[str, object]", test) if isinstance(test, dict) else {}
    suite, status = found.get("suite"), found.get("status")
    one = cast("list[object]", suite) if isinstance(suite, list) else []
    if len(one) != 1 or not isinstance(one[0], str):
      raise ValueError(f"a test has no one suite: {found.get('name')!r}")
    if not isinstance(status, str):
      raise ValueError(f"a test has no status: {found.get('name')!r}")
    if _labels(found).get(EXPECT) == EXPECT_FAILED:
      continue
    passed, total = counts.get(one[0], (0, 0))
    counts[one[0]] = (passed + (status == "passed"), total + 1)
  return counts


def write(report: Mapping[str, object], path: Path) -> None:
  """Write a report as a new file.

  Args:
    report: The report.
    path: Where; nothing may be there, not even a link.
  """
  with path.open("x", encoding="utf-8") as f:
    json.dump(report, f, indent=2)


PREFIX = ".testrunner-"
"""The start of the name of each file testrunner writes into the project for
a run, and removes once it ends."""


@dataclass(frozen=True, slots=True)
class Context:
  """Where a runner runs.

  Attributes:
    app: The project, an absolute path.
    dir: The directory the runner starts in, inside the project.
    work: An empty directory outside the project for the runner's report.
    base: The project's base commit, whose files the configuration is taken
      from, or None to take them as they are.
  """

  app: Path
  dir: Path
  work: Path
  base: str | None

  def base_text(self, path: Path) -> str | None:
    """Return the text a file of the project has at the base.

    Args:
      path: The file, an absolute path inside the project.

    Returns:
      Its text, or None when it has none there.
    """
    rel = path.relative_to(self.app).as_posix()
    if self.base is None:
      if path.is_symlink() or not path.is_file():
        return None
      return path.read_text(encoding="utf-8", errors="replace")
    done = subprocess.run(
      [
        "git",
        "--no-replace-objects",
        "-C",
        str(self.app),
        "cat-file",
        "blob",
        f"{self.base}:{rel}",
      ],
      capture_output=True,
      check=False,
      env=_git_env(),
    )
    if done.returncode != 0:
      return None
    return done.stdout.decode("utf-8", errors="replace")


def _git_env() -> dict[str, str]:
  # Only the repository's own configuration.
  return {
    **os.environ,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
  }


@dataclass(frozen=True, slots=True)
class Call:
  """One command of a run.

  Attributes:
    argv: The command.
    env: What it adds to the environment.
    output: A file outside the project that its standard output and error
      are written to, together, for the adapter to read as its report;
      None leaves them as they are.
  """

  argv: list[str]
  env: Mapping[str, str] = field(default_factory=dict[str, str])
  output: Path | None = None


class Runner(Protocol):
  """A runner's adapter."""

  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    """Ready the project for a run.

    Args:
      ctx: Where it runs.
      tests: The tests it runs.

    Returns:
      The files it wrote into the project, removed once the run ends.
    """
    ...

  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    """Return the commands that run tests, one after another.

    Args:
      ctx: Where it runs.
      tests: The tests to run.

    Returns:
      The commands, run in ctx.dir; none when none of the tests is there to
      run.
    """
    ...

  def cases(self, ctx: Context) -> list[Case] | None:
    """Return the cases of the report a run wrote.

    Args:
      ctx: Where it ran.

    Returns:
      The cases, or None when there is no report.
    """
    ...

  def covers(self, outer: str, inner: str) -> bool:
    """Return whether one ID names a case of the test another names.

    Args:
      outer: An ID, such as a test's.
      inner: Another, such as a case's.

    Returns:
      Whether inner is outer, or one of its cases, such as a parameter or a
      method of a class.
    """
    ...


def rel(ctx: Context, path: Path) -> str:
  """Return a path relative to the project, `/`-separated.

  Args:
    ctx: Where the runner runs.
    path: The path.

  Returns:
    The path from the project's root.
  """
  return Path(os.path.relpath(path, ctx.app)).as_posix()


def write_new(path: Path, text: str) -> Path:
  """Write a file, after removing what is at its name, a link not followed.

  Args:
    path: The file.
    text: Its text.

  Returns:
    The path.
  """
  if os.path.lexists(path):
    path.unlink()
  with path.open("x", encoding="utf-8") as f:
    _ = f.write(text)
  return path


def upward(ctx: Context) -> list[Path]:
  """Return the directories from where the runner starts up to the root.

  Args:
    ctx: Where the runner runs.

  Returns:
    ctx.dir, then each of its parents inside the project, the root last.
  """
  found = [ctx.dir]
  for here in ctx.dir.parents:
    if here != ctx.app and ctx.app not in here.parents:
      break
    found.append(here)
  return found


@dataclass(frozen=True, slots=True)
class Spec:
  """What testrunner knows of a runner, which its module's SPECS gives.

  Attributes:
    adapter: Its adapter's class.
    runner_files: The files it loads by itself that hold only its own
      configuration, which a dataset checks back to the base before the
      tests run: a pattern without `/` matches a file's name, one with `/`
      the end of its path.
    build_config: The build files that also hold its test configuration and
      that no command line replaces, which a dataset keeps at the base;
      patterns as runner_files' are.
    id_sep: What ends the file an ID names, or None when an ID names none.
    restore_paths: What gives, of the base's files, those that define a
      test (restore_paths); None gives its ID's file when the base
      has it.
    files: The package's files it needs beyond its module and the common
      ones (bundle.files).
    vendor_dir: Whether it loads dependencies from the `vendor` directory
      of a test's dir (vendor_test_dirs).
  """

  adapter: type[Runner]
  runner_files: tuple[str, ...] = ()
  build_config: tuple[str, ...] = ()
  id_sep: str | None = None
  restore_paths: Callable[[Test, Collection[str]], list[str]] | None = None
  files: tuple[str, ...] = ()
  vendor_dir: bool = False


def spec(runner: str) -> Spec:
  """Return a runner's Spec, importing its module.

  Args:
    runner: A runner of registry.MODULES.

  Returns:
    The Spec its module's SPECS gives.

  Raises:
    ValueError: When it is not a runner of registry.MODULES; each function
      here that takes a runner raises it so.
  """
  if runner not in MODULES:
    raise ValueError(f"unknown runner: {runner}")
  module = importlib.import_module(f".{MODULES[runner]}", __package__)
  return cast("dict[str, Spec]", module.SPECS)[runner]


def load_adapter(runner: str) -> type[Runner]:
  """Return a runner's adapter.

  Args:
    runner: The runner.

  Returns:
    The adapter's class.
  """
  return spec(runner).adapter


def runner_files(runner: str) -> tuple[str, ...]:
  """Return the patterns of the files a runner loads by itself.

  Args:
    runner: The runner.

  Returns:
    Spec.runner_files: a pattern without `/` matches a file's name, one
    with `/` the end of its path.
  """
  return spec(runner).runner_files


def loaded_by_runner(runner: str, path: str) -> bool:
  """Return whether a runner loads a file by itself.

  Args:
    runner: The runner.
    path: The file's path, relative to the project.

  Returns:
    Whether one of its runner_files matches it.
  """
  return _matches(runner_files(runner), path)


def build_config(runner: str) -> tuple[str, ...]:
  """Return the patterns of a runner's build files that a dataset keeps.

  Args:
    runner: The runner.

  Returns:
    Spec.build_config, patterns as runner_files' are: the build files that
    also hold its test configuration and that no command line replaces.
  """
  return spec(runner).build_config


def is_build_config(runner: str, path: str) -> bool:
  """Return whether a file is one of a runner's build_config.

  Args:
    runner: The runner.
    path: The file's path, relative to the project.

  Returns:
    Whether one of its build_config matches it.
  """
  return _matches(build_config(runner), path)


def id_file(runner: str, id_: str) -> str | None:
  """Return the file a test's ID names.

  Args:
    runner: The test's runner.
    id_: Its ID.

  Returns:
    The ID up to the runner's Spec.id_sep, a path relative to the project;
    None when the runner's IDs name no file.
  """
  sep = spec(runner).id_sep
  return None if sep is None else id_.split(sep, 1)[0]


def restore_paths(test: Test, files: Collection[str]) -> list[str]:
  """Return the files of the base that define a test.

  Args:
    test: The test.
    files: The paths, relative to the project, of the base's files.

  Returns:
    Those of files that hold the test's code, as its runner's
    Spec.restore_paths gives them; without one, its ID's file when files
    has it; none for a test of a run group with a command of its own.
  """
  if test.runner is None:
    return []
  found = spec(test.runner)
  if found.restore_paths is not None:
    return found.restore_paths(test, files)
  path = id_file(test.runner, test.id)
  return [path] if path is not None and path in files else []


def _matches(patterns: tuple[str, ...], path: str) -> bool:
  # A pattern without `/` matches a file's name, one with `/` the end of its
  # path.
  name = path.rsplit("/", 1)[-1]
  for pattern in patterns:
    if "/" in pattern:
      if path == pattern or path.endswith("/" + pattern):
        return True
    elif fnmatch.fnmatchcase(name, pattern):
      return True
  return False


def vendor_test_dirs(tests: Iterable[Test]) -> list[str]:
  """Return the dir of each test whose runner loads its `vendor` directory.

  Args:
    tests: The tests.

  Returns:
    The dir of each of tests whose runner's Spec.vendor_dir is set, in
    their order.
  """
  return [
    t.dir for t in tests if t.runner is not None and spec(t.runner).vendor_dir
  ]


TEST_DIRS = frozenset({"test", "tests", "testing"})
"""The directories at the project's top that hold tests."""

NESTED_TEST_DIRS = frozenset({"spec", "__tests__"})
"""The directories at any depth that hold tests."""

SRC_TESTS = ("src", "test")
"""A directory of sources and the one of tests in it, at any depth."""

TEST_INFIXES = frozenset({"test", "spec"})
"""What between a file name's first and last dots makes it a test's, as
in x.test.<ext>."""

TEST_NAMES = frozenset({"conftest.py"})
"""The names of files of tests wherever they are."""

PLAIN_SOURCES = (".py", ".pyi")
"""The source files that NESTED_TEST_DIRS, SRC_TESTS, a name ending in
`_spec` or TEST_INFIXES do not make a test's, beyond the guards' own
(plain_sources)."""


def guards() -> list[ModuleType]:
  """Return the modules of registry.GUARDS, importing them.

  Returns:
    Each, in GUARDS' order: in a task's copy, only those it has.
  """
  return [importlib.import_module(f".{m}", __package__) for m in GUARDS]


def plain_sources() -> tuple[str, ...]:
  """Return the suffixes of the source files whose names make no test.

  Returns:
    PLAIN_SOURCES and the PLAIN_SOURCES of each module of guards that has
    them.
  """
  found: list[str] = [*PLAIN_SOURCES]
  for module in guards():
    found += cast("tuple[str, ...]", getattr(module, "PLAIN_SOURCES", ()))
  return tuple(found)


def is_test_file(path: str) -> bool:
  """Return whether a path is a test's file, by its parts alone.

  Args:
    path: The path, relative to the project.

  Returns:
    Whether its top directory is one of TEST_DIRS, its name starts with
    `test_`, its stem ends with `_test` or it is one of TEST_NAMES; or,
    for a name that ends with none of plain_sources, whether a directory
    of it is one of NESTED_TEST_DIRS or the second of SRC_TESTS after the
    first, its stem ends with `_spec`, or one of TEST_INFIXES is between
    its first and last dots. Directories are compared lowercased.
  """
  p = PurePosixPath(path)
  dirs = [d.lower() for d in p.parts[:-1]]
  top = dirs[0] if dirs else ""
  if (
    top in TEST_DIRS
    or p.name.startswith("test_")
    or p.stem.endswith("_test")
    or p.name in TEST_NAMES
  ):
    return True
  return not p.name.endswith(plain_sources()) and (
    any(d in NESTED_TEST_DIRS for d in dirs)
    or SRC_TESTS in itertools.pairwise(dirs)
    or p.stem.endswith("_spec")
    or bool(TEST_INFIXES & set(p.name.split(".")[1:-1]))
  )


def is_test_dir(path: str) -> bool:
  """Return whether a path is a directory of tests, by is_test_file's rule.

  Args:
    path: The path, relative to the project.

  Returns:
    Whether it is one of TEST_DIRS at the top, or ends with one of
    NESTED_TEST_DIRS or with SRC_TESTS, compared lowercased.
  """
  parts = PurePosixPath(path.lower()).parts
  if not parts:
    return False
  return (
    (len(parts) == 1 and parts[0] in TEST_DIRS)
    or parts[-1] in NESTED_TEST_DIRS
    or parts[-2:] == SRC_TESTS
  )


Spans = list[tuple[int, int]]
"""Spans of a file's lines, each its first and last line, from 1."""


def kept_lines(app: Path, tests: Sequence[Test]) -> dict[str, Spans]:
  """Return the lines of the base's files that a fix must leave as they are.

  They are lines that hold listed tests in a file a fix may change too, so
  that `restore` does not check it back, as the kept_lines of the modules
  of guards give them.

  Args:
    app: The project at the base.
    tests: The manifest's tests.

  Returns:
    For each such file, by its path from the project's root, its spans,
    each once.
  """
  found: dict[str, Spans] = {}
  for module in guards():
    hook = getattr(module, "kept_lines", None)
    if hook is None:
      continue
    for path, spans in cast("dict[str, Spans]", hook(app, tests)).items():
      have = found.setdefault(path, [])
      have += [s for s in spans if s not in have]
  return found


def rewires(path: str, added: str) -> str | None:
  """Return what lines a fix adds to a file change what the tests check.

  That is a change to the tests' meaning with no change to them, such as a
  macro they assert with defined again, as the rewires of the modules of
  guards find it.

  Args:
    path: The file's path, relative to the project.
    added: The lines added, joined by newlines.

  Returns:
    The first such thing a module of guards finds, in a few words; None
    for none.
  """
  for module in guards():
    hook = getattr(module, "rewires", None)
    found = None if hook is None else cast("str | None", hook(path, added))
    if found is not None:
      return found
  return None


_GIT = (
  "git",
  "--no-replace-objects",
  "-c",
  "core.hooksPath=/dev/null",
  "-c",
  "core.fsmonitor=false",
)
_MODES = {"100644": 0o644, "100755": 0o755}
_LINK = "120000"


def globs(manifest: Manifest, base_files: Collection[str]) -> list[str]:
  """Return the globs of what a manifest's tests need at the base.

  Args:
    manifest: The manifest.
    base_files: The paths, relative to the project, of the base's files.

  Returns:
    Its [restore] paths, then for each runner its tests name, the files of
    runner_files anywhere in the project, then the files restore_paths
    gives for its tests, each once.
  """
  found = list(manifest.restore)
  runners = dict.fromkeys(t.runner for t in manifest.tests if t.runner)
  for runner in runners:
    found += [f"{ANY}/{p}" for p in runner_files(runner)]
  for test in manifest.tests:
    if test.runner:
      found += [escape(p) for p in restore_paths(test, base_files)]
  return list(dict.fromkeys(found))


def restore(app: Path, base: str, globs: Iterable[str]) -> list[str]:
  """Check back to the base the files some globs match.

  Args:
    app: The project, a git repository with the base commit.
    base: The base commit.
    globs: The globs.

  Returns:
    The paths written or deleted, sorted; a file that is already as at the
    base is not written. A git command that fails raises
    subprocess.CalledProcessError.
  """
  chosen = list(globs)
  entries = _tree(app, base)
  done: list[str] = []
  for path in _git(
    app, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
  ):
    if path not in entries and matches(path, chosen):
      target = app / path
      if os.path.lexists(target) and not _is_dir(target):
        target.unlink()
        done.append(path)
  wanted = {
    p: e
    for p, e in entries.items()
    if (e.mode in _MODES or e.mode == _LINK) and matches(p, chosen)
  }
  blobs = _blobs(app, [e.sha for e in wanted.values()])
  for path, entry in wanted.items():
    if _write(app, path, entry.mode, blobs[entry.sha]):
      done.append(path)
  return sorted(done)


def remove(app: Path, globs: Iterable[str]) -> list[str]:
  """Delete what some globs match in a project, a link not followed.

  Args:
    app: The project.
    globs: The globs.

  Returns:
    The paths deleted, sorted; a directory is deleted with what it holds,
    a `.git` in it too. The project itself and its own `.git` are never
    deleted, and no `.git` is matched or walked into.
  """
  found = {p for one in globs for p in _matched(app, one)}
  done: list[str] = []
  for path in sorted(found):
    target = app / path
    if not os.path.lexists(target):
      continue
    if _is_dir(target):
      shutil.rmtree(target)
    else:
      target.unlink()
    done.append(path)
  return done


def _matched(app: Path, one: str) -> set[str]:
  # The paths under the project a glob matches, walked from the directory
  # its leading components name, through no link and not into a .git.
  start = app
  for part in literal_prefix(one):
    if not _is_dir(start) or part == GIT:
      return set()
    start = start / part
  if not os.path.lexists(start):
    return set()
  found: set[str] = set()
  if start != app and matches(_rel(app, start), [one]):
    found.add(_rel(app, start))
  if not _is_dir(start):
    return found
  for root, dirs, files in os.walk(start):
    dirs[:] = [d for d in dirs if d != GIT]
    for name in [*dirs, *files]:
      if name == GIT:
        continue
      path = _rel(app, Path(root, name))
      if matches(path, [one]):
        found.add(path)
  return found


@dataclass(frozen=True, slots=True)
class _Entry:
  mode: str
  sha: str


def _tree(app: Path, base: str) -> dict[str, _Entry]:
  entries: dict[str, _Entry] = {}
  for line in _git(app, "ls-tree", "-r", "-z", "--full-tree", base):
    meta, _, path = line.partition("\t")
    mode, _, sha = meta.split(" ")
    entries[path] = _Entry(mode, sha)
  return entries


def _blobs(app: Path, shas: list[str]) -> dict[str, bytes]:
  if not shas:
    return {}
  unique = list(dict.fromkeys(shas))
  done = subprocess.run(
    [*_GIT, "-C", str(app), "cat-file", "--batch"],
    input="".join(f"{s}\n" for s in unique).encode(),
    capture_output=True,
    check=True,
    env=_env(),
  )
  out, at = done.stdout, 0
  found: dict[str, bytes] = {}
  for sha in unique:
    end = out.index(b"\n", at)
    header = out[at:end].split(b" ")
    if len(header) != 3 or header[0].decode() != sha:
      raise ValueError(f"git cat-file gave no blob for {sha}")
    size = int(header[2])
    found[sha] = out[end + 1 : end + 1 + size]
    at = end + 1 + size + 1
  return found


def _write(app: Path, path: str, mode: str, data: bytes) -> bool:
  target = app / path
  if _same(target, mode, data):
    return False
  here = app
  for part in path.split("/")[:-1]:
    here = here / part
    if not _is_dir(here):
      if os.path.lexists(here):
        here.unlink()
      here.mkdir()
  if _is_dir(target):
    shutil.rmtree(target)
  elif os.path.lexists(target):
    target.unlink()
  if mode == _LINK:
    os.symlink(os.fsdecode(data), target)
    return True
  with target.open("xb") as f:
    _ = f.write(data)
  target.chmod(_MODES[mode])
  return True


def _same(target: Path, mode: str, data: bytes) -> bool:
  try:
    st = os.lstat(target)
  except FileNotFoundError:
    return False
  if mode == _LINK:
    return stat.S_ISLNK(st.st_mode) and os.readlink(target) == os.fsdecode(data)
  if not stat.S_ISREG(st.st_mode) or st.st_size != len(data):
    return False
  executable = bool(st.st_mode & 0o100)
  return executable == (mode == "100755") and target.read_bytes() == data


def _is_dir(path: Path) -> bool:
  return path.is_dir() and not path.is_symlink()


def _rel(app: Path, path: Path) -> str:
  return path.relative_to(app).as_posix()


def _git(app: Path, *args: str) -> list[str]:
  done = subprocess.run(
    [*_GIT, "-C", str(app), *args],
    capture_output=True,
    check=True,
    env=_env(),
  )
  return [p for p in os.fsdecode(done.stdout).split("\0") if p]


def _env() -> dict[str, str]:
  return {
    **os.environ,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
  }


LIMIT = 64 * 1024 * 1024
"""The most bytes a report's file may have."""

SUFFIXES = {"junit": ".xml", "ctrf": ".json"}
"""The suffix of a format's files under a report's directory."""

_STATUSES = {
  "passed": "passed",
  "failed": "failed",
  "skipped": "skipped",
  "pending": "skipped",
  "other": "failed",
}


class ReportError(ValueError):
  """A report that cannot be read."""


def read_report(format_: str, path: Path) -> list[Case] | None:
  """Return the cases of a report.

  Args:
    format_: One of FORMATS.
    path: The report: a file, or a directory whose files of the format,
      at any depth and in the order of their paths, are read; a link is
      not followed.

  Returns:
    The cases in the files' order, or None when there is no file.

  Raises:
    ReportError: When a file cannot be read.
  """
  files = _report_files(path, SUFFIXES[format_])
  if not files:
    return None
  cases: list[Case] = []
  for file in files:
    if file.stat().st_size > LIMIT:
      raise ReportError(f"{file.name} is over {LIMIT} bytes")
    data = file.read_bytes()
    cases += _junit(data) if format_ == "junit" else _ctrf(data)
  return cases


def _report_files(path: Path, suffix: str) -> list[Path]:
  if path.is_symlink():
    return []
  if path.is_file():
    return [path]
  found: list[Path] = []
  for root, dirs, names in os.walk(path):
    dirs.sort()
    found += [
      Path(root, n)
      for n in sorted(names)
      if n.endswith(suffix)
      and not Path(root, n).is_symlink()
      and Path(root, n).is_file()
    ]
  return found


def _junit(data: bytes) -> list[Case]:
  # Imported here: a task's copy has junit.py only when a run group's
  # parse or a runner reads JUnit XML (bundle.files).
  junit = importlib.import_module(".junit", __package__)
  return cast("Callable[[bytes], list[Case]]", junit.report_cases)(data)


def _ctrf(data: bytes) -> list[Case]:
  try:
    report = cast("object", json.loads(data.decode("utf-8")))
  except (UnicodeDecodeError, ValueError) as e:
    raise ReportError("a report is not UTF-8 JSON") from e
  results = _dict(report).get("results")
  tests = _dict(results).get("tests")
  if not isinstance(tests, list):
    raise ReportError("a report has no results.tests array")
  return [_case(t) for t in cast("list[object]", tests)]


def _dict(value: object) -> dict[str, object]:
  return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _case(value: object) -> Case:
  test = _dict(value)
  name, status = test.get("name"), test.get("status")
  if not isinstance(name, str) or not name.strip() or "\n" in name:
    raise ReportError(f"a test has no one-line name: {name!r}")
  if not isinstance(status, str) or status not in _STATUSES:
    raise ReportError(f"{name}: status {status!r} is not CTRF's")
  error = test.get("rawStatus") == ERROR
  return Case(
    name,
    "failed" if error else _STATUSES[status],
    _duration(test.get("duration")),
    error,
    _text(test.get("message")),
    _text(test.get("trace")),
    _text(test.get("filePath")),
  )


def _duration(value: object) -> int:
  if isinstance(value, bool) or not isinstance(value, int | float):
    return 0
  return round(value) if math.isfinite(value) and value > 0 else 0


def _text(value: object) -> str | None:
  return value if isinstance(value, str) and value else None


# What the runners must not take from the environment of whoever runs this.
_DROPPED_ENV = ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")

OUT_ENV = "TESTRUNNER_OUT"
"""The variable that names a run group's new, empty report directory."""

DIR_ENV = "TESTRUNNER_DIR"
"""The variable that names the directory a run group starts in."""

CTRF_ENV = "TESTRUNNER_CTRF"
"""The variable that names the file a parse argv writes its CTRF to."""

UNLISTED_LIMIT = 1000
"""How many of a run group's unlisted names a report keeps."""


@dataclass(frozen=True, slots=True)
class Results:
  """What a run of a manifest's tests found.

  Attributes:
    tests: A test object for each case of a listed test the runners
      reported, and one for each listed test they did not: the runs in the
      order the manifest first names each, the tests of a run in its order.
    unlisted: For each run group with a command of its own that reported
      names the manifest does not list, `{run, total, names}`: its name,
      how many such names, and at most UNLISTED_LIMIT of them, sorted.
  """

  tests: list[TestObject]
  unlisted: list[dict[str, object]] = field(
    default_factory=list[dict[str, object]]
  )


def run(
  manifest: Manifest, *, tests: Path, app: Path, base: str | None = None
) -> Results:
  """Run a manifest's tests.

  The manifest's files are copied into the project first. Each suite's tests
  of one runner and directory, or of one preset's run group, are run
  together, apart from the other suites', so that a suite that cannot even
  load, such as tests of a fix not there yet, leaves the others' alone. A
  run group with a command of its own runs it once for all its tests.

  Args:
    manifest: The manifest.
    tests: The task's tests/ directory, which the files are copied from.
    app: The project.
    base: The project's base commit, which the runners' configuration is
      taken from; None takes it from the files as they are.

  Returns:
    What the runs found.
  """
  app = app.resolve()
  tests = tests.resolve()
  place(manifest.files, tests, app)
  groups = {g.name: g for g in manifest.runs}
  found = Results([])
  with tempfile.TemporaryDirectory(prefix="testrunner-") as work:
    for n, listed in enumerate(_groups(manifest.tests)):
      here = Path(work) / str(n)
      here.mkdir()
      first = listed[0]
      if first.runner is None:
        group = groups[str(first.run)]
        print(f"===== {group.name} {group.dir} =====", flush=True)
        objects, names = run_own(group, tests, app, here, listed)
        found.tests.extend(objects)
        if names:
          found.unlisted.append(
            {
              "run": group.name,
              "total": len(names),
              "names": names[:UNLISTED_LIMIT],
            }
          )
        continue
      ctx = Context(app, app / first.dir, here, base)
      print(f"===== {first.suite} {first.runner} {first.dir} =====", flush=True)
      found.tests.extend(run_group(load_adapter(first.runner)(), ctx, listed))
  return found


def _groups(tests: tuple[Test, ...]) -> Iterator[list[Test]]:
  # The tests that name their runner, by suite, runner and dir; those of a
  # preset's run group, by suite and group; those of a group with a command
  # of its own, by group.
  groups: dict[tuple[str | None, ...], list[Test]] = {}
  for test in tests:
    suite = test.suite if test.runner is not None else None
    key = (suite, test.runner, test.dir, test.run)
    groups.setdefault(key, []).append(test)
  yield from groups.values()


def run_own(
  group: Run, tests_dir: Path, app: Path, work: Path, tests: list[Test]
) -> tuple[list[TestObject], list[str]]:
  """Run a run group's own command and read its report.

  The group's outputs are deleted, its run argv runs in its dir with
  TESTRUNNER_OUT a new, empty directory under work and TESTRUNNER_DIR its
  dir, and its report is read: from TESTRUNNER_OUT in a format testrunner
  reads, or from the file TESTRUNNER_CTRF names, which its parse argv
  writes after the run, in a directory made once the run has ended. An
  argument of either argv that is a relative path of a regular file under
  tests/ is given as that file's absolute path. How each ended counts for
  nothing but the message of a test the report lacks.

  Args:
    group: The run group.
    tests_dir: The task's tests/ directory, resolved.
    app: The project, resolved.
    work: An empty directory outside the project.
    tests: The group's tests.

  Returns:
    The tests' objects, as run gives them: a case belongs to the test whose
    id is its name; and the names no test has, sorted.
  """
  where = app / group.dir
  if not where.is_dir() or where.resolve() != where:
    return [
      missing(t, "its dir is not a directory of the project") for t in tests
    ], []
  _ = remove(app, group.outputs)
  out = work / "out"
  out.mkdir()
  env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
  env |= {OUT_ENV: str(out), DIR_ENV: str(where)}
  ends = [_own(tests_dir, group.run or (), where, env, "run")]
  if isinstance(group.parse, str):
    report, format_ = out, group.parse
  else:
    parsed = work / "parsed"
    parsed.mkdir()
    report, format_ = parsed / FILE, "ctrf"
    env[CTRF_ENV] = str(report)
    ends.append(_own(tests_dir, group.parse or (), where, env, "parse"))
  why = ", ".join(ends)
  try:
    cases = read_report(format_, report)
  except ReportError as e:
    return [missing(t, f"{why}; {e}") for t in tests], []
  if cases is None:
    return [missing(t, f"{why}, with no report") for t in tests], []
  mine: dict[str, list[Case]] = {t.id: [] for t in tests}
  unlisted: set[str] = set()
  for case in cases:
    if case.name in mine:
      mine[case.name].append(case)
    else:
      unlisted.add(case.name)
  found: list[TestObject] = []
  for test in tests:
    if not mine[test.id]:
      found.append(missing(test, why))
    for case in mine[test.id]:
      found.append(entry(case, test.suite, labels(test)))
  return found, sorted(unlisted)


def _own(
  tests_dir: Path,
  argv: tuple[str, ...],
  where: Path,
  env: dict[str, str],
  what: str,
) -> str:
  # Runs a group's argv and says how it ended.
  args = [_from_tests(tests_dir, a) for a in argv]
  try:
    done = subprocess.run(args, cwd=where, env=env, check=False)
  except OSError as e:
    return f"the {what} did not start: {e.strerror}"
  return f"the {what} exited {done.returncode}"


def _from_tests(tests_dir: Path, arg: str) -> str:
  # A relative path of a regular file under tests/, through no link, is
  # given as that file's absolute path.
  if relative(arg) is None:
    return arg
  path = tests_dir
  for part in arg.split("/"):
    path = path / part
    if path.is_symlink():
      return arg
  return str(path) if path.is_file() else arg


def run_group(
  runner: Runner, ctx: Context, tests: list[Test]
) -> list[TestObject]:
  """Run tests of one suite, runner and directory.

  Args:
    runner: The runner's adapter.
    ctx: Where it runs.
    tests: The tests.

  Returns:
    Their test objects, as run gives them.
  """
  if not ctx.dir.is_dir() or ctx.dir.resolve() != ctx.dir:
    return [
      missing(t, "its dir is not a directory of the project") for t in tests
    ]
  written = runner.prepare(ctx, tests)
  try:
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
    calls = runner.commands(ctx, tests)
    if not calls:
      return [missing(t, "none of its files is there") for t in tests]
    ends: list[str] = []
    for call in calls:
      try:
        ends.append(f"exited {_call(call, ctx, {**env, **call.env})}")
      except OSError as e:  # the runner is not there
        ends.append(f"did not start: {e.strerror}")
    why = "the runner " + ", ".join(dict.fromkeys(ends))
    cases = runner.cases(ctx)
  finally:
    for path in written:
      path.unlink(missing_ok=True)
  if cases is None:
    return [missing(t, f"{why}, with no report") for t in tests]
  return match(runner, tests, cases, why)


def _call(call: Call, ctx: Context, env: dict[str, str]) -> int:
  # Runs a call; one whose output is its report has it shown afterwards
  # too, for the verifier's log.
  if call.output is None:
    return subprocess.run(
      call.argv, cwd=ctx.dir, env=env, check=False
    ).returncode
  with call.output.open("xb") as out:
    done = subprocess.run(
      call.argv,
      cwd=ctx.dir,
      env=env,
      stdout=out,
      stderr=subprocess.STDOUT,
      check=False,
    )
  text = call.output.read_text(encoding="utf-8", errors="replace")
  print(text, end="", flush=True)
  return done.returncode


def match(
  runner: Runner, tests: list[Test], cases: list[Case], why: str = ""
) -> list[TestObject]:
  """Return the test objects of the cases of the tests that name them.

  A case belongs to the longest ID of the tests that cover it; one no test
  covers is left out. An errored case that covers a test, such as an error
  in loading its file, belongs to each such test that has no case of its
  own.

  Args:
    runner: The runner's adapter.
    tests: The tests of one suite.
    cases: The cases the runner reported.
    why: What is known of how the runner ended, for a test with no case.

  Returns:
    The test objects, as run gives them.
  """
  mine: dict[str, list[Case]] = {t.id: [] for t in tests}
  for case in cases:
    owners = [t.id for t in tests if runner.covers(t.id, case.name)]
    if owners:
      mine[max(owners, key=len)].append(case)
  for test in tests:
    if not mine[test.id]:
      mine[test.id] = [
        c
        for c in cases
        if c.error and c.name != test.id and runner.covers(c.name, test.id)
      ]
  found: list[TestObject] = []
  for test in tests:
    if not mine[test.id]:
      found.append(missing(test, why))
    for case in mine[test.id]:
      found.append(entry(case, test.suite, labels(test)))
  return found


def place(files: tuple[File, ...], tests: Path, app: Path) -> None:
  """Copy a manifest's files into the project, over what is there.

  Args:
    files: The files.
    tests: The directory they are copied from.
    app: The project.

  Raises:
    ValueError: When a source is not a regular file, or a directory on the
      way to a dest is a link or not a directory.
  """
  for file in files:
    source = tests / file.source
    if source.is_symlink() or not source.is_file():
      raise ValueError(f"{file.source} is not a regular file")
    dest = app
    for part in file.dest.split("/")[:-1]:
      dest = dest / part
      if not os.path.lexists(dest):
        dest.mkdir()
      elif not stat.S_ISDIR(os.lstat(dest).st_mode):
        raise ValueError(f"{file.dest}: {part} is not a directory")
    dest = app / file.dest
    if os.path.lexists(dest):
      if stat.S_ISDIR(os.lstat(dest).st_mode):
        shutil.rmtree(dest)
      else:
        dest.unlink()
    with source.open("rb") as src, dest.open("xb") as out:
      shutil.copyfileobj(src, out)

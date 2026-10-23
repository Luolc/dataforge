"""The manifest of a task's tests, tests/manifest.toml.

  [[run]]      a run group: name, and optionally dir, the directory under
               the project it starts in (default "."); and either preset,
               one of RUNNERS, or run, an argv, parse, a format of
               parse.FORMATS or an argv, and optionally outputs, globs of
               the reports the framework writes inside the project
  [restore]    paths, globs of the project's files checked back to the base
               before the tests run (restore.restore)
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
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from typing import cast

from .globs import valid
from .registry import MODULES

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

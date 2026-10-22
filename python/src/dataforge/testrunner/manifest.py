"""The manifest of a task's tests, tests/manifest.toml.

  [[test]]     suite, runner, id; and optionally dir, the directory under the
               project the runner starts in (default "."), kind and
               principle
  [[file]]     source, a file under tests/, and dest, where under the
               project it is copied before the tests run
  [no_tests]   <suite> = why the suite has no test

Only the structure is checked here: the types, that each test's (suite, id)
and each file's dest appear once, that the paths stay inside their
directories, that a suite of no_tests has no test, and, when the caller
gives them, that each principle is one of the task's and that no dest is a
file of the project's base. The values a kind or a suite may take, and
which suite may have which kind, are the dataset's to check.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from typing import cast

NAME = "manifest.toml"
"""The manifest's name in a task's tests/."""

RUNNERS = (
  "pytest",
  "jest",
  "vitest",
  "jasmine",
  "node",
  "go",
  "cargo",
  "ctest",
  "gtest",
  "maven",
  "phpunit",
)
"""The runners a test may name."""

SUITE = re.compile(r"[a-z][a-z0-9_]*")
"""What a suite's name matches, as a whole; it holds no `::`."""

_TEST_KEYS = {"suite", "runner", "id", "dir", "kind", "principle"}
_FILE_KEYS = {"source", "dest"}
_TOP_KEYS = {"test", "file", "no_tests"}


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
    runner: The runner that runs it, one of RUNNERS.
    id: Its ID in the runner's own form, paths relative to the project.
    dir: The directory, relative to the project, the runner starts in.
    kind: What it checks, in the dataset's words, or None.
    principle: The ID of the principle it checks, or None.
  """

  suite: str
  runner: str
  id: str
  dir: str = "."
  kind: str | None = None
  principle: str | None = None

  @property
  def ref(self) -> str:
    """The reference to it, `<suite>::<id>`, unique in a manifest."""
    return f"{self.suite}::{self.id}"


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
  """

  tests: tuple[Test, ...]
  files: tuple[File, ...]
  no_tests: Mapping[str, str]


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
  tests = _tests(data.get("test", []), principles, problems)
  files = _files(data.get("file", []), base_files, problems)
  no_tests = _no_tests(data.get("no_tests", {}), tests, problems)
  if problems:
    raise ManifestError(problems)
  return Manifest(tuple(tests), tuple(files), no_tests)


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
  value: object, principles: Collection[str] | None, problems: list[str]
) -> list[Test]:
  tests: list[Test] = []
  seen: set[tuple[str, str]] = set()
  for n, table in enumerate(_tables(value, "test", problems), 1):
    where = f"test {n}"
    problems += [
      f"{where}: unknown key {k!r}" for k in table if k not in _TEST_KEYS
    ]
    suite = _string(table, "suite", where, problems)
    if suite and not SUITE.fullmatch(suite):
      problems.append(f"{where}: suite {suite!r} is not a plain name")
    runner = _string(table, "runner", where, problems)
    if runner and runner not in RUNNERS:
      problems.append(f"{where}: unknown runner {runner!r}")
    id_ = _string(table, "id", where, problems)
    where = f"test {n} ({suite}::{id_})"
    if (suite, id_) in seen:
      problems.append(f"{where}: listed twice")
    seen.add((suite, id_))
    dir_ = relative(table.get("dir", "."), dot=True)
    if dir_ is None:
      problems.append(f"{where}: dir is not a path inside the project")
    kind = _optional(table, "kind", where, problems)
    principle = _optional(table, "principle", where, problems)
    if principle and principles is not None and principle not in principles:
      problems.append(f"{where}: no principle {principle!r}")
    tests.append(Test(suite, runner, id_, dir_ or ".", kind, principle))
  return tests


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

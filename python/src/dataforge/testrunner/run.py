"""Running the tests of a manifest on a project."""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from . import ctrf
from .adapter import Context, Runner
from .ctrf import Case, TestObject
from .manifest import File, Manifest, Test
from .runners import ADAPTERS

# What the runners must not take from the environment of whoever runs this.
_DROPPED_ENV = ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")


def run(
  manifest: Manifest, *, tests: Path, app: Path, base: str | None = None
) -> list[TestObject]:
  """Run a manifest's tests and return their CTRF test objects.

  The manifest's files are copied into the project first. Each suite's tests
  of one runner and directory are run together, apart from the other
  suites', so that a suite that cannot even load, such as tests of a fix not
  there yet, leaves the others' alone.

  Args:
    manifest: The manifest.
    tests: The task's tests/ directory, which the files are copied from.
    app: The project.
    base: The project's base commit, which the runners' configuration is
      taken from; None takes it from the files as they are.

  Returns:
    A test object for each case of a listed test the runners reported, and
    one for each listed test they did not: the runs in the order the
    manifest first names each, the tests of a run in its order.
  """
  app = app.resolve()
  place(manifest.files, tests, app)
  found: list[TestObject] = []
  with tempfile.TemporaryDirectory(prefix="testrunner-") as work:
    for n, (key, group) in enumerate(_groups(manifest.tests)):
      suite, runner, where = key
      here = Path(work) / str(n)
      here.mkdir()
      ctx = Context(app, app / where, here, base)
      print(f"===== {suite} {runner} {where} =====", flush=True)
      found += run_group(ADAPTERS[runner](), ctx, group)
  return found


def _groups(
  tests: tuple[Test, ...],
) -> Iterator[tuple[tuple[str, str, str], list[Test]]]:
  groups: dict[tuple[str, str, str], list[Test]] = {}
  for test in tests:
    groups.setdefault((test.suite, test.runner, test.dir), []).append(test)
  yield from groups.items()


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
      ctrf.missing(t, "its dir is not a directory of the project")
      for t in tests
    ]
  written = runner.prepare(ctx, tests)
  try:
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
    calls = runner.commands(ctx, tests)
    if not calls:
      return [ctrf.missing(t, "none of its files is there") for t in tests]
    ends: list[str] = []
    for call in calls:
      try:
        done = subprocess.run(
          call.argv, cwd=ctx.dir, env={**env, **call.env}, check=False
        )
        ends.append(f"exited {done.returncode}")
      except OSError as e:  # the runner is not there
        ends.append(f"did not start: {e.strerror}")
    why = "the runner " + ", ".join(dict.fromkeys(ends))
    cases = runner.cases(ctx)
  finally:
    for path in written:
      path.unlink(missing_ok=True)
  if cases is None:
    return [ctrf.missing(t, f"{why}, with no report") for t in tests]
  return match(runner, tests, cases, why)


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
      found.append(ctrf.missing(test, why))
    for case in mine[test.id]:
      found.append(ctrf.entry(case, test.suite, ctrf.labels(test)))
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

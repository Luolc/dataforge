"""Running the tests of a manifest on a project."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from . import ctrf, parse
from .adapter import Call, Context, Runner
from .ctrf import Case, TestObject
from .manifest import File, Manifest, relative, Run, Test
from .restore import remove
from .runners import ADAPTERS

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
      found.tests.extend(run_group(ADAPTERS[first.runner](), ctx, listed))
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
      ctrf.missing(t, "its dir is not a directory of the project")
      for t in tests
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
    report, format_ = parsed / ctrf.FILE, "ctrf"
    env[CTRF_ENV] = str(report)
    ends.append(_own(tests_dir, group.parse or (), where, env, "parse"))
  why = ", ".join(ends)
  try:
    cases = parse.read(format_, report)
  except parse.ReportError as e:
    return [ctrf.missing(t, f"{why}; {e}") for t in tests], []
  if cases is None:
    return [ctrf.missing(t, f"{why}, with no report") for t in tests], []
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
      found.append(ctrf.missing(test, why))
    for case in mine[test.id]:
      found.append(ctrf.entry(case, test.suite, ctrf.labels(test)))
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
        ends.append(f"exited {_call(call, ctx, {**env, **call.env})}")
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

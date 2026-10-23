"""The adapters of the Node.js runners: jest, vitest, Jasmine and node:test.

A test's ID is its file, from the project's root, and the titles of its
describe blocks and its own, each after ` > `; an ID of a file or a describe
block covers the tests in it. An ID names a file, not a directory: one that
names a directory runs nothing and is an error. Each runner is the
project's own, the first node_modules/.bin/<runner> from the directory up
to the root, else the one on the PATH; node:test is the `node` on the PATH.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import math
import os
from pathlib import Path
from typing import cast, final, override
import xml.etree.ElementTree as ET

from .adapter import Call, Context, PREFIX, rel, Runner, Spec, upward, write_new
from .ctrf import Case
from .manifest import Test

JS_SEP = " > "
"""What joins a test's file and titles in its ID."""

JASMINE_REPORTER = Path(__file__).parent / "jasmine_reporter.cjs"
"""The Jasmine reporter that writes a run's outcomes as JSON."""

JASMINE_REPORT_ENV = "TESTRUNNER_JASMINE_REPORT"
"""The variable that tells the reporter where to write."""


def node_id(file: str, titles: list[str]) -> str:
  """Return the ID of a test.

  Args:
    file: Its file, from the project's root.
    titles: The titles of its describe blocks, outermost first, and its own.

  Returns:
    The ID.
  """
  return JS_SEP.join([file, *titles])


def covers(outer: str, inner: str) -> bool:
  """Return whether an ID names a test or covers it, as a file or a block.

  Args:
    outer: An ID, such as a test's.
    inner: Another, such as a case's.

  Returns:
    Whether inner is outer or inside it.
  """
  return inner == outer or inner.startswith(outer + JS_SEP)


def files_of(ctx: Context, tests: list[Test]) -> list[str]:
  """Return the files of tests that are there, relative to the directory.

  Args:
    ctx: Where the runner runs.
    tests: The tests.

  Returns:
    Each file once, in the tests' order.
  """
  found: list[str] = []
  for test in tests:
    path = ctx.app / test.id.split(JS_SEP, 1)[0]
    arg = os.path.relpath(path, ctx.dir)
    if path.is_file() and arg not in found:
      found.append(arg)
  return found


def find_bin(ctx: Context, name: str) -> str:
  """Return the project's own copy of a Node.js tool, or its bare name.

  Args:
    ctx: Where the runner runs.
    name: The tool, such as jest.

  Returns:
    The first node_modules/.bin/<name> from the directory up to the
    project's root, where a workspace may have hoisted it; else name, for
    the PATH to resolve.
  """
  for here in (ctx.dir, *ctx.dir.parents):
    found = here / "node_modules" / ".bin" / name
    if found.is_file():
      return str(found)
    if here == ctx.app:
      break
  return name


def _json(path: Path) -> dict[str, object] | None:
  if path.is_symlink() or not path.is_file():
    return None
  try:
    found = cast("object", json.loads(path.read_text(encoding="utf-8")))
  except (ValueError, UnicodeDecodeError):
    return None
  return cast("dict[str, object]", found) if isinstance(found, dict) else None


def _list(value: object) -> list[object]:
  return cast("list[object]", value) if isinstance(value, list) else []


def _dict(value: object) -> dict[str, object]:
  return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
  return value if isinstance(value, str) and value else None


def _ms(value: object) -> int:
  if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
    return round(value)
  return 0


def jest_cases(ctx: Context, report: dict[str, object]) -> list[Case]:
  """Return the cases of a jest-style JSON report, as jest and vitest write.

  Args:
    ctx: Where the runner ran.
    report: The report.

  Returns:
    A case for each test, and an errored one named as its file for a file
    that ran no test because it failed to load.
  """
  cases: list[Case] = []
  for result in map(_dict, _list(report.get("testResults"))):
    name = _text(result.get("name"))
    if name is None:
      continue
    file = rel(ctx, Path(name))
    tests = [_dict(a) for a in _list(result.get("assertionResults"))]
    if not tests and result.get("status") == "failed":
      message = _text(result.get("message"))
      cases.append(Case(file, "failed", error=True, message=message, file=file))
    for test in tests:
      titles = [
        t for t in _list(test.get("ancestorTitles")) if isinstance(t, str)
      ]
      title = _text(test.get("title")) or ""
      raw = test.get("status")
      status = raw if raw in ("passed", "failed") else "skipped"
      failures = [
        m for m in _list(test.get("failureMessages")) if isinstance(m, str)
      ]
      cases.append(
        Case(
          node_id(file, [*titles, title]),
          cast("str", status),
          _ms(test.get("duration")),
          message=failures[0].split("\n", 1)[0] if failures else None,
          trace="\n\n".join(failures) or None,
          file=file,
        )
      )
  return cases


def _config_copy(ctx: Context, names: tuple[str, ...]) -> Path | None:
  # Writes beside it a copy of the base of the first of names, nearest
  # first, that the base has.
  for here in upward(ctx):
    for name in names:
      text = ctx.base_text(here / name)
      if text is not None:
        return write_new(here / f"{PREFIX}{name}", text)
  return None


@final
class Jest(Runner):
  """jest, run on the tests' files with `--runTestsByPath`.

  The configuration is what jest itself would find at the base, nearest
  first: a jest.config.* as a copy of its base beside it, a package.json's
  `jest` as JSON, and a package.json without one, or nothing, as no
  configuration but its directory as the root; it is given with --config,
  so that what a fix adds to package.json or a new jest.config.* is not
  read. Babel's configuration, which jest's default transform reads, is a
  file of RUNNER_FILES, but for package.json's `babel`, which no command
  line replaces: a dataset checks it itself, as Maven's pom.xml.
  """

  _CONFIGS = tuple(
    f"jest.config.{e}" for e in ("js", "ts", "mjs", "mts", "cjs", "cts", "json")
  )
  _REPORT = "jest.json"

  def __init__(self) -> None:
    """Make the adapter, for one run."""
    self._config = ""

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    for here in upward(ctx):
      for name in self._CONFIGS:
        text = ctx.base_text(here / name)
        if text is not None:
          copy = write_new(here / f"{PREFIX}{name}", text)
          self._config = str(copy)
          return [copy]
      package = ctx.base_text(here / "package.json")
      if package is not None:
        return self._from_package(ctx, here, package)
    self._config = json.dumps({"rootDir": str(ctx.dir)})
    return []

  def _from_package(self, ctx: Context, here: Path, text: str) -> list[Path]:
    # A package.json's `jest`: a path to a configuration file, or the
    # configuration, whose rootDir is the package's directory by default.
    try:
      jest = _dict(cast("object", json.loads(text))).get("jest")
    except ValueError:
      jest = None
    if isinstance(jest, str):
      path = (here / jest).resolve()
      base = ctx.base_text(path) if ctx.app in path.parents else None
      if base is not None:
        copy = write_new(path.parent / f"{PREFIX}{path.name}", base)
        self._config = str(copy)
        return [copy]
    config = _dict(jest)
    root = config.get("rootDir")
    config["rootDir"] = str(here / root) if isinstance(root, str) else str(here)
    self._config = json.dumps(config)
    return []

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    files = files_of(ctx, tests)
    if not files:
      return []
    argv = [find_bin(ctx, "jest"), "--ci", "--json"]
    argv += [f"--outputFile={ctx.work / self._REPORT}"]
    argv += [f"--config={self._config}", "--runTestsByPath", *files]
    return [Call(argv)]

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    report = _json(ctx.work / self._REPORT)
    return None if report is None else jest_cases(ctx, report)

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return covers(outer, inner)


@final
class Vitest(Runner):
  """vitest, run as `vitest run` on the tests' files.

  The configuration is the first vitest.config.* or vite.config.* the base
  has, nearest first, as vitest finds it, given with --config as a copy of
  its base beside it; with none, an empty one. So a fix's change to
  vite.config.*, which also builds the project, is not read.
  """

  _CONFIGS = tuple(
    f"{n}.config.{e}"
    for n in ("vitest", "vite")
    for e in ("ts", "mts", "cts", "js", "mjs", "cjs")
  )
  _REPORT = "vitest.json"

  def __init__(self) -> None:
    """Make the adapter, for one run."""
    self._config = Path()

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    self._config = _config_copy(ctx, self._CONFIGS) or write_new(
      ctx.dir / f"{PREFIX}vitest.config.mjs", "export default {};\n"
    )
    return [self._config]

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    files = files_of(ctx, tests)
    if not files:
      return []
    report = ctx.work / self._REPORT
    argv = [find_bin(ctx, "vitest"), "run", f"--config={self._config}"]
    argv += ["--reporter=default", "--reporter=json"]
    argv += [f"--outputFile.json={report}", *files]
    return [Call(argv)]

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    report = _json(ctx.work / self._REPORT)
    return None if report is None else jest_cases(ctx, report)

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return covers(outer, inner)


@final
class Jasmine(Runner):
  """Jasmine, run on each test file alone with testrunner's own reporter.

  One file at a time, since a file that fails to load ends Jasmine's run
  with no report, and since Jasmine before 4 gives a spec no file. The
  configuration is the one Jasmine finds itself, spec/support/jasmine.*,
  which is one of RUNNER_FILES.
  """

  def __init__(self) -> None:
    """Make the adapter, for one run."""
    self._files: list[tuple[str, Path]] = []

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    return []

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    self._files = []
    calls: list[Call] = []
    for n, file in enumerate(files_of(ctx, tests)):
      report = ctx.work / f"jasmine-{n}.json"
      self._files.append((rel(ctx, ctx.dir / file), report))
      argv = [find_bin(ctx, "jasmine"), f"--reporter={JASMINE_REPORTER}", file]
      calls.append(Call(argv, {JASMINE_REPORT_ENV: str(report)}))
    return calls

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    cases: list[Case] = []
    reported = False
    for file, path in self._files:
      report = _json(path)
      if report is None:
        continue
      reported = True
      for error in _list(report.get("errors")):
        cases.append(
          Case(file, "failed", error=True, message=_text(error), file=file)
        )
      for spec in map(_dict, _list(report.get("specs"))):
        titles = [t for t in _list(spec.get("titles")) if isinstance(t, str)]
        raw = spec.get("status")
        status = raw if raw in ("passed", "failed") else "skipped"
        cases.append(
          Case(
            node_id(file, titles),
            cast("str", status),
            _ms(spec.get("duration")),
            spec.get("error") is True,
            _text(spec.get("message")),
            _text(spec.get("trace")),
            file,
          )
        )
    return cases if reported else None

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return covers(outer, inner)


@final
class Node(Runner):
  """node:test, run as `node --test` on each test file alone.

  One file at a time, since the JUnit report of Node before 22 names no
  file. Its JUnit reporter (`--test-reporter=junit`) nests a describe block,
  and a test with subtests, as a testsuite; a file that fails to load is a
  test named as the file.
  """

  def __init__(self) -> None:
    """Make the adapter, for one run."""
    self._files: list[tuple[str, Path]] = []

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    return []

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    self._files = []
    calls: list[Call] = []
    for n, file in enumerate(files_of(ctx, tests)):
      report = ctx.work / f"node-{n}.xml"
      self._files.append((rel(ctx, ctx.dir / file), report))
      argv = ["node", "--test", "--test-reporter=spec"]
      argv += ["--test-reporter-destination=stdout", "--test-reporter=junit"]
      argv += [f"--test-reporter-destination={report}", file]
      calls.append(Call(argv))
    return calls

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    # Imported here: only node reads JUnit XML, and a task's copy has
    # junit.py only for a runner that needs it (bundle.files).
    from . import junit

    cases: list[Case] = []
    reported = False
    for file, path in self._files:
      root = junit.root(path)
      if root is None:
        continue
      reported = True
      itself = {
        file,
        str(ctx.app / file),
        os.path.relpath(ctx.app / file, ctx.dir),
      }
      for titles, tc in _walk(root, []):
        end = junit.outcome(tc)
        whole = not titles[:-1] and titles[-1] in itself
        cases.append(
          Case(
            file if whole else node_id(file, titles),
            end.status,
            end.duration,
            end.error or (whole and end.status == "failed"),
            end.message,
            end.trace,
            file,
          )
        )
    return cases if reported else None

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return covers(outer, inner)


def _walk(
  element: ET.Element, titles: list[str]
) -> Iterator[tuple[list[str], ET.Element]]:
  # Each testcase under element, with the names of its testsuites and its
  # own.
  for child in element:
    name = child.get("name") or ""
    if child.tag == "testsuite":
      yield from _walk(child, [*titles, name])
    elif child.tag == "testcase":
      yield [*titles, name], child


SPECS = {
  "jest": Spec(
    Jest,
    runner_files=("jest.config.*", "babel.config.*", ".babelrc", ".babelrc.*"),
    id_sep=JS_SEP,
  ),
  "vitest": Spec(
    Vitest,
    runner_files=("vitest.config.*", "vitest.workspace.*"),
    id_sep=JS_SEP,
  ),
  "jasmine": Spec(
    Jasmine,
    runner_files=(
      "spec/support/jasmine.json",
      "spec/support/jasmine.js",
      "spec/support/jasmine.mjs",
    ),
    id_sep=JS_SEP,
    files=("jasmine_reporter.cjs",),
  ),
  "node": Spec(Node, id_sep=JS_SEP, files=("junit.py",)),
}
"""The Spec of each Node.js runner; jest's runner_files include Babel's
configuration, which jest's default transform reads by itself."""

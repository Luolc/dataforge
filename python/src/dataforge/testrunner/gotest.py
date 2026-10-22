"""The adapter of go test.

A test's ID is `<import path>::<TestName>`, a subtest's with its `/`-joined
names as go gives them (a space made `_`); an import path alone covers its
package's tests, and a test its subtests. The tests run as `go test -json
-count=1` on their packages, in the directory, so a passed result is not
taken from go's test cache, and with `-run` choosing their top-level tests,
whose subtests all run; when a package is listed whole there is no `-run`,
and every listed package runs whole. What runs and is not listed is left
out.
"""

from __future__ import annotations

from collections.abc import Collection
import json
from pathlib import Path, PurePosixPath
import re
from typing import cast, final, override

from .adapter import Call, Context, Runner
from .ctrf import Case
from .manifest import Test

SEP = "::"
"""What joins a test's import path and name in its ID."""


def covers(outer: str, inner: str) -> bool:
  """Return whether an ID names a test or covers it, as a package or test.

  Args:
    outer: An ID, such as a test's.
    inner: Another, such as a case's.

  Returns:
    Whether inner is outer, a test of its package or a subtest of it.
  """
  if inner == outer:
    return True
  if SEP not in outer:
    return inner.startswith(outer + SEP)
  return inner.startswith(outer + "/")


def _names(tests: list[Test]) -> tuple[list[str], list[str] | None]:
  # The packages, in the tests' order, and the top-level test names to run;
  # None when a package is listed whole.
  packages: list[str] = []
  names: list[str] | None = []
  for test in tests:
    package, _, name = test.id.partition(SEP)
    if package not in packages:
      packages.append(package)
    if not name:
      names = None
    elif names is not None:
      top = name.split("/", 1)[0]
      if top not in names:
        names.append(top)
  return packages, names


@final
class GoTest(Runner):
  """go test, its `-json` events read from its output.

  A package that does not build, or fails with no test of its own failing
  (such as a TestMain that exits early), is an errored case named as the
  package, which its listed tests with no case of their own take as theirs;
  a test that starts and never ends, as when another of its package panics
  or the package times out, is an errored case of its own.
  """

  _REPORT = "go.json"

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    return []

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    packages, names = _names(tests)
    argv = ["go", "test", "-json", "-count=1"]
    if names is not None:
      argv.append("-run=^(?:" + "|".join(map(re.escape, names)) + ")$")
    return [Call([*argv, *packages], output=ctx.work / self._REPORT)]

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    path = ctx.work / self._REPORT
    if path.is_symlink() or not path.is_file():
      return None
    return go_cases(path.read_text(encoding="utf-8", errors="replace"))

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return covers(outer, inner)


def go_cases(text: str) -> list[Case]:
  """Return the cases of go test -json's output.

  Args:
    text: The output; a line that is not an event, such as one on standard
      error, is left out.

  Returns:
    A case for each test that started or ended, in the order each first
    appears, and an errored one named as its package for each package that
    did not build or failed with no test of its own failing.
  """
  events = _Events()
  for line in text.splitlines():
    event = _event(line)
    if event is not None:
      events.add(event)
  return [c for key in events.order if (c := events.case(key)) is not None]


@final
class _Events:
  # The events of one run, by package and test ("" for the package itself).

  def __init__(self) -> None:
    self.order: list[tuple[str, str]] = []
    self.ends: dict[tuple[str, str], tuple[str, float]] = {}
    self.output: dict[tuple[str, str], list[str]] = {}
    self.failed: set[str] = set()

  def add(self, event: dict[str, object]) -> None:
    action = event.get("Action")
    package = event.get("Package") or event.get("ImportPath")
    if not isinstance(package, str) or not isinstance(action, str):
      return
    package = package.split(" ", 1)[0]
    test = event.get("Test")
    key = (package, test if isinstance(test, str) else "")
    whole = action in ("fail", "build-fail") and not key[1]
    if key not in self.order and (key[1] or whole):
      self.order.append(key)
    out = event.get("Output")
    if action in ("output", "build-output") and isinstance(out, str):
      self.output.setdefault(key, []).append(out)
    elif action in ("pass", "fail", "skip") and key[1]:
      self.ends[key] = (action, _seconds(event.get("Elapsed")))
    elif whole:
      self.failed.add(package)

  def case(self, key: tuple[str, str]) -> Case | None:
    package, test = key
    trace = "".join(self.output.get(key, [])) or None
    if not test:
      if package not in self.failed or any(
        p == package and self.ends.get((p, t), ("",))[0] == "fail"
        for p, t in self.order
      ):
        return None
      return Case(
        package, "failed", error=True, message=_first(trace), trace=trace
      )
    end = self.ends.get(key)
    name = f"{package}{SEP}{test}"
    if end is None:
      return Case(
        name, "failed", error=True, message="it did not end", trace=trace
      )
    status = {"pass": "passed", "fail": "failed"}.get(end[0], "skipped")
    message = _first(trace) if status == "failed" else None
    return Case(
      name, status, round(end[1] * 1000), message=message, trace=trace
    )


def _event(line: str) -> dict[str, object] | None:
  if not line.startswith("{"):
    return None
  try:
    found = cast("object", json.loads(line))
  except ValueError:
    return None
  return cast("dict[str, object]", found) if isinstance(found, dict) else None


def _seconds(value: object) -> float:
  if isinstance(value, (int, float)) and value > 0:
    return float(value)
  return 0.0


def _first(trace: str | None) -> str | None:
  # The first line of output that says something, past go's own `=== RUN`
  # and `--- FAIL` lines and the `# <package>` line before a build's errors.
  for line in (trace or "").splitlines():
    text = line.strip()
    if text and not text.startswith(("=== ", "--- ", "# ")):
      return text
  return None


def restore_paths(test: Test, files: Collection[str]) -> list[str]:
  """Return the base's test files of a go test's package.

  The package's directory is the one under the test's dir whose path is the
  longest tail of the import path, as a module lays its packages out; a
  package at the module's root, whose directory is no tail of it, gives
  none.

  Args:
    test: The test.
    files: The paths, relative to the project, of the base's files.

  Returns:
    The `_test.go` files directly in that directory.
  """
  parts = test.id.split(SEP, 1)[0].split("/")
  under = PurePosixPath(test.dir)
  dirs = {PurePosixPath(f).parent for f in files if f.endswith("_test.go")}
  for n in range(1, len(parts)):
    found = under.joinpath(*parts[n:])
    if found in dirs:
      return sorted(
        f
        for f in files
        if f.endswith("_test.go") and PurePosixPath(f).parent == found
      )
  return []

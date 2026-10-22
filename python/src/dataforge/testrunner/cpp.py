"""The adapters of the C and C++ runners: CTest and GoogleTest.

Both run in a CMake build directory, the test's dir, that the task's image
has configured and built: each call first configures it again with the
cache it has (`cmake .`), so that a test file a glob takes in is built too,
then builds what it runs, and runs the tests only when both succeed, so
that a binary the build left as it was is never run. Their reports are
JUnit XML.

CTest's ID is a test's name as `ctest -N` lists it, which for a test
gtest_discover_tests adds is GoogleTest's name with its parameter's value
(`Inst/P.Even/2`). GoogleTest's is `<binary>::<name>`, the binary its path
from the directory, named as its CMake target, and the name GoogleTest's
full one (`Suite.Test`, `Inst/Suite.Test/0`); a binary covers its tests, a
suite its tests (`<binary>::Suite`) and a parametrized test its instances.
An ID names no source file, so restore_paths gives none: a dataset restores
the files that define its tests itself.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import shlex
from typing import final, override

from . import junit
from .adapter import Call, Context, rel, Runner
from .ctrf import Case
from .manifest import Test

SEP = "::"
"""What joins a GoogleTest test's binary and name in its ID."""

_CONFIGURE = "cmake . && cmake --build ."


def _cmake_regex(text: str) -> str:
  # CMake's regular expressions take a special character escaped with `\`.
  return re.sub(r"([\\^$.|?*+()\[\]])", r"\\\1", text)


@final
class CTest(Runner):
  """CTest, run as `ctest --output-junit` on the listed tests by name.

  `--output-junit` needs CMake 3.21 or later.
  """

  _REPORT = "ctest.xml"

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    return []

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    names = "|".join(_cmake_regex(t.id) for t in tests)
    ctest = ["ctest", "--output-on-failure"]
    ctest += ["--output-junit", str(ctx.work / self._REPORT)]
    ctest += ["-R", f"^({names})$"]
    return [Call(["sh", "-c", f"{_CONFIGURE} && exec {shlex.join(ctest)}"])]

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    found = junit.testcases(ctx.work / self._REPORT)
    if found is None:
      return None
    cases: list[Case] = []
    for tc in found:
      end = junit.marked_outcome(tc)
      # CTest's failure says only that it failed; the test's output says why.
      out = (tc.findtext("system-out") or "").strip() or None
      trace = end.trace or (out if end.status != "passed" else None)
      cases.append(
        Case(
          tc.get("name") or "",
          end.status,
          end.duration,
          end.error,
          end.message,
          trace,
        )
      )
    return cases

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return inner == outer


def covers(outer: str, inner: str) -> bool:
  """Return whether a GoogleTest ID names a test or covers it.

  Args:
    outer: An ID, such as a test's.
    inner: Another, such as a case's.

  Returns:
    Whether inner is outer, or a test of its binary or suite, or an
    instance of it.
  """
  if inner == outer:
    return True
  if SEP not in outer:
    return inner.startswith(outer + SEP)
  return inner.startswith((outer + ".", outer + "/"))


def _filter(names: list[str]) -> str:
  # A suite runs whole, and a parametrized test each of its instances.
  patterns: list[str] = []
  for name in names:
    patterns += [f"{name}.*"] if "." not in name else [name, f"{name}/*"]
  return ":".join(patterns)


@final
class GTest(Runner):
  """GoogleTest, each listed binary built and run with --gtest_output=xml."""

  def __init__(self) -> None:
    """Make the adapter, for one run."""
    self._reports: list[tuple[str, Path]] = []

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    return []

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    binaries: dict[str, list[str] | None] = {}
    for test in tests:
      binary, _, name = test.id.partition(SEP)
      known = binaries.setdefault(binary, [])
      if not name:
        binaries[binary] = None
      elif known is not None and name not in known:
        known.append(name)
    self._reports = []
    calls: list[Call] = []
    for n, (binary, names) in enumerate(binaries.items()):
      report = ctx.work / f"gtest-{n}.xml"
      self._reports.append((binary, report))
      run = [f"./{binary}", f"--gtest_output=xml:{report}"]
      if names is not None:
        run.append(f"--gtest_filter={_filter(names)}")
      target = shlex.quote(PurePosixPath(binary).name)
      script = f"{_CONFIGURE} --target {target} && exec {shlex.join(run)}"
      calls.append(Call(["sh", "-c", script]))
    return calls

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    cases: list[Case] = []
    reported = False
    for binary, path in self._reports:
      found = junit.testcases(path)
      if found is None:
        continue
      reported = True
      for tc in found:
        end = junit.marked_outcome(tc)
        name = f"{tc.get('classname') or ''}.{tc.get('name') or ''}"
        file = tc.get("file")
        inside = file and ctx.app in Path(file).parents
        cases.append(
          Case(
            f"{binary}{SEP}{name}",
            end.status,
            end.duration,
            end.error,
            end.message,
            end.trace,
            rel(ctx, Path(file)) if file and inside else None,
          )
        )
    return cases if reported else None

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return covers(outer, inner)

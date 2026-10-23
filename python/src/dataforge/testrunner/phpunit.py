"""The adapter of PHPUnit."""

from __future__ import annotations

from collections.abc import Collection
import os
from pathlib import Path, PurePosixPath
import re
from typing import final, override
import xml.etree.ElementTree as ET

from . import junit
from .adapter import Call, Context, rel, Runner, Spec
from .ctrf import Case
from .manifest import Test

RUNNER_FILES = ("phpunit.xml", "phpunit.dist.xml", "phpunit.xml.dist")
"""The configuration files PHPUnit finds by itself."""


@final
class PHPUnit(Runner):
  r"""PHPUnit, run as the project's vendor/bin/phpunit, else phpunit.

  An ID is `<class>::<method>`, or a class for all its methods, the class by
  its full name with `\`; a data set's case belongs to its method. The
  configuration is the directory's phpunit.xml as PHPUnit would find it, a
  file of RUNNER_FILES, given with --configuration; without one, a
  configuration of the run's own whose one test suite is the files named for
  the listed classes, outside vendor/, the --filter choosing the tests in
  them.
  """

  _REPORT = "phpunit.xml"
  _SUITE = "phpunit-tests.xml"

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    if _php_config(ctx) is None:
      found = _php_files(ctx, tests)
      if found:
        suite = ET.Element("testsuite", name="testrunner")
        for file in found:
          ET.SubElement(suite, "file").text = str(file)
        top = ET.Element("phpunit")
        ET.SubElement(top, "testsuites").append(suite)
        ET.ElementTree(top).write(ctx.work / self._SUITE, encoding="utf-8")
    return []

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    config = _php_config(ctx) or ctx.work / self._SUITE
    if not config.is_file():
      return []
    local = ctx.dir / "vendor" / "bin" / "phpunit"
    names = "|".join(_pcre(t.id) for t in tests)
    argv = [
      str(local) if local.is_file() else "phpunit",
      f"--configuration={config}",
      "--do-not-cache-result",
      f"--log-junit={ctx.work / self._REPORT}",
      f"--filter=/^(?:{names})(?:::| |$)/",
    ]
    return [Call(argv)]

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    found = junit.testcases(ctx.work / self._REPORT)
    if found is None:
      return None
    cases: list[Case] = []
    for tc in found:
      cls = tc.get("class") or (tc.get("classname") or "").replace(".", "\\")
      name = tc.get("name") or ""
      node = f"{cls}::{name}" if cls else name
      file = tc.get("file")
      inside = file and ctx.app in Path(file).parents
      end = junit.outcome(tc)
      cases.append(
        Case(
          node,
          end.status,
          end.duration,
          end.error,
          end.message,
          end.trace,
          rel(ctx, Path(file)) if inside and file else None,
        )
      )
    return cases

  @override
  def covers(self, outer: str, inner: str) -> bool:
    if inner == outer:
      return True
    if "::" not in outer:
      return inner.startswith(outer + "::")
    return inner.startswith(outer + " ")


def _php_config(ctx: Context) -> Path | None:
  for name in RUNNER_FILES:
    if (ctx.dir / name).is_file():
      return ctx.dir / name
  return None


def _php_files(ctx: Context, tests: list[Test]) -> list[Path]:
  # The files named for a listed test's class, as PHPUnit wants a test
  # class's file named.
  names = {t.id.split("::", 1)[0].rsplit("\\", 1)[-1] + ".php" for t in tests}
  found: list[Path] = []
  for root, dirs, files in os.walk(ctx.dir):
    dirs[:] = sorted(d for d in dirs if d not in _PHP_PRUNE)
    found += [Path(root, f) for f in sorted(files) if f in names]
  return found


_PHP_PRUNE = frozenset({".git", "node_modules", "vendor"})


def _pcre(text: str) -> str:
  # Each character but a letter, digit or _ escaped, which PCRE takes as
  # itself.
  return re.sub(r"(\W)", r"\\\1", text)


def restore_paths(test: Test, files: Collection[str]) -> list[str]:
  """Return the files of the base that define a test.

  Args:
    test: The test.
    files: The paths, relative to the project, of the base's files.

  Returns:
    The files named for its class under its directory, as PHPUnit wants.
  """
  under = "" if test.dir == "." else test.dir + "/"
  name = test.id.split("::", 1)[0].rsplit("\\", 1)[-1] + ".php"
  return sorted(
    f for f in files if f.startswith(under) and PurePosixPath(f).name == name
  )


SPECS = {
  "phpunit": Spec(
    PHPUnit,
    runner_files=RUNNER_FILES,
    restore_paths=restore_paths,
    files=("junit.py",),
  )
}
"""PHPUnit's Spec."""

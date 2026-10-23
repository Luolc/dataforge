"""The adapter of Maven Surefire.

An ID's file is not in the ID: restore_paths finds it by the outer class.
"""

from __future__ import annotations

from collections.abc import Collection
import os
from pathlib import Path
from typing import final, override

from . import junit
from .adapter import Call, Context, Runner, Spec
from .ctrf import Case
from .manifest import Test

RUNNER_FILES = (
  "junit-platform.properties",
  ".mvn/maven.config",
  ".mvn/jvm.config",
  ".mvn/extensions.xml",
)
"""The files Maven or the JUnit Platform loads by itself."""


@final
class Maven(Runner):
  """Maven Surefire, run as `mvn -o test` with `-Dtest` naming the tests.

  An ID is `<class>#<method>`, or a class for all its methods, the class by
  its full name; a parameter's case belongs to its method. The reports are
  every TEST-*.xml of a target/surefire-reports under the directory, those
  of an earlier run removed first, since Surefire takes no other directory
  from the command line.

  Maven cannot take its test configuration from the base: it is in pom.xml,
  which a fix may change and which Maven reads only from where it is. The
  command line turns back on what a changed pom.xml could turn off, and a
  listed test it keeps from running is an error; but a changed pom.xml can
  also replace what runs under a listed name (another testSourceDirectory)
  or what is reported (a plugin bound to the test phase after Surefire),
  and nothing here sees that. A dataset that runs Maven tests checks
  pom.xml itself, for example against the base's.
  """

  _PRUNE = frozenset({".git", "node_modules"})

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    for report in self._reports(ctx):
      report.unlink()
    return []

  def _reports(self, ctx: Context) -> list[Path]:
    found: list[Path] = []
    for root, dirs, _ in os.walk(ctx.dir):
      if os.path.basename(root) == "target":
        dirs[:] = [d for d in dirs if d == "surefire-reports"]
      else:
        dirs[:] = sorted(d for d in dirs if d not in self._PRUNE)
      if os.path.basename(root) == "surefire-reports":
        here = Path(root)
        if here.parent.name == "target":
          found += [
            p
            for p in sorted(here.glob("TEST-*.xml"))
            if p.is_file() and not p.is_symlink()
          ]
    return found

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    # A class listed whole runs whole, whatever methods of it are listed.
    whole = {t.id for t in tests if "#" not in t.id}
    methods: dict[str, list[str]] = {}
    for test in tests:
      cls, _, method = test.id.partition("#")
      _ = methods.setdefault(cls, [])
      if method and cls not in whole:
        methods[cls].append(method)
    filters = [f"{c}#{'+'.join(m)}" if m else c for c, m in methods.items()]
    argv = [
      "mvn",
      "-o",
      "-B",
      "-fae",
      f"-Dtest={','.join(filters)}",
      "-Dsurefire.failIfNoSpecifiedTests=false",
      "-DfailIfNoTests=false",
      "-DskipTests=false",
      "-Dmaven.test.skip=false",
      "-Dmaven.test.failure.ignore=true",
      "test",
    ]
    return [Call(argv)]

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    reports = self._reports(ctx)
    if not reports:
      return None
    found: list[Case] = []
    for report in reports:
      for tc in junit.testcases(report) or []:
        cls, name = tc.get("classname") or "", tc.get("name") or ""
        node = cls if name in ("", cls) else f"{cls}#{name}"
        end = junit.outcome(tc)
        found.append(
          Case(
            node, end.status, end.duration, end.error, end.message, end.trace
          )
        )
    return found

  @override
  def covers(self, outer: str, inner: str) -> bool:
    if inner == outer:
      return True
    if "#" not in outer:
      return inner.startswith(outer + "#")
    return inner.startswith((outer + "(", outer + "["))


def restore_paths(test: Test, files: Collection[str]) -> list[str]:
  """Return the files of the base that define a test.

  Args:
    test: The test.
    files: The paths, relative to the project, of the base's files.

  Returns:
    The src/test/java or src/test/kotlin files of its outer class under its
    directory.
  """
  under = "" if test.dir == "." else test.dir + "/"
  cls = test.id.split("#", 1)[0].split("$", 1)[0]
  ends = [
    f"src/test/{lang}/{cls.replace('.', '/')}.{ext}"
    for lang, ext in (("java", "java"), ("kotlin", "kt"))
  ]
  return sorted(
    f for f in files if f.startswith(under) and any(_ends(f, e) for e in ends)
  )


def _ends(path: str, end: str) -> bool:
  return path == end or path.endswith("/" + end)


SPECS = {
  "maven": Spec(
    Maven,
    runner_files=RUNNER_FILES,
    build_config=("pom.xml",),
    restore_paths=restore_paths,
    files=("junit.py",),
  )
}
"""Maven's Spec: pom.xml holds its test configuration, which no command line
replaces."""

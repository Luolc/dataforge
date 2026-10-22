"""How each runner is started on a project and how its report is read.

A runner is started once for each suite and directory, on the tests of the
manifest that name it there, and writes its report outside the project. A
file that only the runner loads by itself (RUNNER_FILES) is for the dataset
to check back to the project's base before the tests run. A build file that
also holds the runner's configuration, which a fix may need to change, is
not: its configuration is taken from the base and given to the runner on the
command line, where the runner allows it; and a test the runner then leaves
out is an error all the same (ctrf.missing). A project file that the base's
configuration names to run in the test process (pytest's `-p` plugins, jest's
and vitest's setup files, Jasmine's helpers) is neither: it is the dataset's
to restore, as it restores conftest.py.
"""

from __future__ import annotations

from collections.abc import Collection
import configparser
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tomllib
from typing import final, override
import xml.etree.ElementTree as ET

from . import cargo, gotest, junit
from .adapter import Call, Context, PREFIX, rel, Runner, write_new
from .cargo import Cargo
from .cpp import CTest, GTest
from .ctrf import Case
from .gotest import GoTest
from .js import Jasmine, Jest, Node, SEP, Vitest
from .manifest import Test

RUNNER_FILES: dict[str, tuple[str, ...]] = {
  "pytest": (
    "conftest.py",
    "pytest.ini",
    ".pytest.ini",
    "pytest.toml",
    ".pytest.toml",
    "tox.ini",
    "sitecustomize.py",
    "usercustomize.py",
    "*.pth",
  ),
  "jest": ("jest.config.*", "babel.config.*", ".babelrc", ".babelrc.*"),
  "vitest": ("vitest.config.*", "vitest.workspace.*"),
  "jasmine": (
    "spec/support/jasmine.json",
    "spec/support/jasmine.js",
    "spec/support/jasmine.mjs",
  ),
  "node": (),
  "go": (),
  "cargo": (".cargo/config.toml", ".cargo/config"),
  "ctest": ("CTestCustom.cmake", "CTestCustom.ctest"),
  "gtest": (),
  "maven": (
    "junit-platform.properties",
    ".mvn/maven.config",
    ".mvn/jvm.config",
    ".mvn/extensions.xml",
  ),
  "phpunit": ("phpunit.xml", "phpunit.dist.xml", "phpunit.xml.dist"),
}
"""For each runner, the files it loads by itself that hold only its own
configuration, or, for jest, that of Babel, which jest's default transform
reads by itself: a pattern without `/` matches a file's name, one with `/`
the end of its path."""


def loaded_by_runner(runner: str, path: str) -> bool:
  """Return whether a runner loads a file by itself.

  Args:
    runner: The runner.
    path: The file's path, relative to the project.

  Returns:
    Whether one of the runner's RUNNER_FILES matches it.
  """
  name = path.rsplit("/", 1)[-1]
  for pattern in RUNNER_FILES[runner]:
    if "/" in pattern:
      if path == pattern or path.endswith("/" + pattern):
        return True
    elif fnmatch.fnmatchcase(name, pattern):
      return True
  return False


@final
class Pytest(Runner):
  """pytest, run with the interpreter that runs this, through pytest_main.

  pytest and its plugins are loaded from the interpreter's installation and
  PYTHONPATH's entries outside the project: the directory pytest starts in,
  PYTHONPATH's entries inside the project and the configuration's pythonpath
  join sys.path only once pytest starts loading conftest files, in the order
  `python -m pytest` gives them. So a file a fix adds, such as a pytest.py,
  cannot stand in for pytest or a plugin.

  An ID is a node ID relative to the project; a parameter's case, `[...]`,
  and the tests of a directory, file or class belong to the ID that names
  them. The configuration is the first file pytest itself would pick at the
  base, from the common ancestor of the tests' paths up,
  given with `-c`: a copy of its base beside it, but for a pytest.toml,
  which pytest knows only by its name and which is given as it is, being
  one of RUNNER_FILES; and with none, an empty one. So a section a fix adds
  to a build file such as pyproject.toml is not read.
  """

  # pytest's order: _pytest/config/findpaths.py's locate_config.
  _CONFIGS = (
    "pytest.toml",
    ".pytest.toml",
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
  )
  _AS_IS = ("pytest.toml", ".pytest.toml")
  _REPORT = "pytest.xml"

  def __init__(self) -> None:
    """Make the adapter, for one run."""
    self._config = Path()
    self._rootdir = Path()

  @override
  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    found = self._find(ctx, tests)
    if found is None:
      self._rootdir = ctx.dir
      self._config = write_new(ctx.dir / f"{PREFIX}pytest.ini", "[pytest]\n")
      return [self._config]
    path, text = found
    self._rootdir = path.parent
    if path.name in self._AS_IS or ctx.base is None:
      self._config = path
      return []
    name = f"{PREFIX}{path.name.lstrip('.')}"
    self._config = write_new(path.parent / name, text)
    return [self._config]

  def _find(self, ctx: Context, tests: list[Test]) -> tuple[Path, str] | None:
    # From the common ancestor of the tests' paths up, as pytest's
    # determine_setup looks.
    paths = [p for p in _paths(ctx, tests) if os.path.lexists(p)]
    if not paths:
      return None
    start = Path(os.path.commonpath(paths))
    if start.is_file():
      start = start.parent
    for here in (start, *start.parents):
      if not (here == ctx.app or ctx.app in here.parents):
        break
      for name in self._CONFIGS:
        text = ctx.base_text(here / name)
        if text is not None and _has_pytest(name, text):
          return here / name, text
    return None

  @override
  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    # Files, not node IDs: pytest runs nothing when one ID is not found.
    paths: list[str] = []
    for path in _paths(ctx, tests):
      arg = os.path.relpath(path, ctx.dir)
      if os.path.lexists(path) and arg not in paths:
        paths.append(arg)
    if not paths:
      return []
    entries, outside = _pythonpath(ctx)
    argv = [
      sys.executable,
      "-P",
      str(_MAIN),
      json.dumps(
        {
          "paths": [str(ctx.dir), *entries],
          "pythonpath": os.environ.get("PYTHONPATH"),
        }
      ),
      *paths,
      f"-c={self._config}",
      f"--rootdir={self._rootdir}",
      "-p",
      "no:cacheprovider",
      "--continue-on-collection-errors",
      "-o",
      "junit_family=xunit1",
      f"--junitxml={ctx.work / self._REPORT}",
      "-rA",
    ]
    return [Call(argv, {"PYTHONPATH": os.pathsep.join(outside)})]

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    found = junit.testcases(ctx.work / self._REPORT)
    if found is None:
      return None
    return [self._case(ctx, tc) for tc in found]

  def _case(self, ctx: Context, tc: ET.Element) -> Case:
    classname, name = tc.get("classname") or "", tc.get("name") or ""
    if classname:
      node = _node_id(self._rootdir, classname, name)
    else:  # an error of a file's collection, named as its module
      node = _node_id(self._rootdir, name, "")
    if node is None:
      node = _from_file(tc.get("file") or "", classname, name)
    path, sep, rest = node.partition("::")
    node = rel(ctx, self._rootdir / path) + sep + rest
    end = junit.outcome(tc)
    return Case(
      node,
      end.status,
      end.duration,
      end.error,
      end.message,
      end.trace,
      node.split("::", 1)[0],
    )

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return inner == outer or inner.startswith(
      (outer + "::", outer + "[", outer + "/")
    )


_MAIN = Path(__file__).with_name("pytest_main.py")


def _pythonpath(ctx: Context) -> tuple[list[str], list[str]]:
  # PYTHONPATH's entries, absolute as Python takes them from the directory
  # pytest starts in, an empty one being that directory; and those of them
  # outside the project, as they are or once links are resolved.
  value = os.environ.get("PYTHONPATH")
  if not value:
    return [], []
  found = [
    os.path.abspath(os.path.join(ctx.dir, e)) for e in value.split(os.pathsep)
  ]
  apps = (ctx.app, Path(os.path.realpath(ctx.app)))
  return found, [
    e
    for e in found
    if not any(
      Path(p).is_relative_to(a) for p in (e, os.path.realpath(e)) for a in apps
    )
  ]


def _node_id(rootdir: Path, dotted: str, name: str) -> str | None:
  # pytest's JUnit classname is the node ID's file, `/` made `.` and `.py`
  # cut, then its classes; the file is the longest prefix that exists.
  parts = dotted.split(".")
  for n in range(len(parts), 0, -1):
    file = "/".join(parts[:n]) + ".py"
    if (rootdir / file).is_file():
      return "::".join([file, *parts[n:], *([name] if name else [])])
  return None


def _paths(ctx: Context, tests: list[Test]) -> list[Path]:
  # The path each test's ID names, a file or a directory.
  return [ctx.app / t.id.split("::", 1)[0] for t in tests]


def _from_file(file: str, classname: str, name: str) -> str:
  # The node ID from xunit1's file attribute, when the classname's file is
  # not found by its dotted name, as in a directory with a `.` in its name:
  # the classes are what the classname has beyond the file's module.
  if not classname:
    return file or name
  module = file.removesuffix(".py").replace("/", ".")
  classes = (
    classname[len(module) + 1 :] if classname.startswith(module + ".") else ""
  )
  return "::".join([file, *filter(None, classes.split(".")), name])


def _has_pytest(name: str, text: str) -> bool:
  # Whether pytest takes a file as its configuration.
  if name in ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini"):
    return True
  if name == "pyproject.toml":
    try:
      return "pytest" in tomllib.loads(text).get("tool", {})
    except tomllib.TOMLDecodeError:
      return False
  parser = configparser.ConfigParser(interpolation=None, strict=False)
  try:
    parser.read_string(text)
  except configparser.Error:
    return False
  return parser.has_section("pytest" if name == "tox.ini" else "tool:pytest")


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
  for name in RUNNER_FILES["phpunit"]:
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


ADAPTERS: dict[str, type[Runner]] = {
  "pytest": Pytest,
  "jest": Jest,
  "vitest": Vitest,
  "jasmine": Jasmine,
  "node": Node,
  "go": GoTest,
  "cargo": Cargo,
  "ctest": CTest,
  "gtest": GTest,
  "maven": Maven,
  "phpunit": PHPUnit,
}
"""The adapter of each runner."""


def restore_paths(test: Test, files: Collection[str]) -> list[str]:
  """Return the files of the base that define a test.

  Args:
    test: The test.
    files: The paths, relative to the project, of the base's files.

  Returns:
    Those of files that hold the test's code: for pytest and the Node.js
    runners, its ID's file; for maven, a src/test/java or src/test/kotlin
    file of its outer class under its directory; for phpunit, a file named
    for its class under its directory, as PHPUnit wants; for go, the test
    files of its package (gotest.restore_paths); for cargo, its ID's file
    when that is a test target of its own (cargo.restore_paths); for ctest
    and gtest, none, since an ID names no source file.
  """
  if test.runner in ("ctest", "gtest"):
    return []
  if test.runner == "go":
    return gotest.restore_paths(test, files)
  if test.runner == "cargo":
    return cargo.restore_paths(test, files)
  under = "" if test.dir == "." else test.dir + "/"
  if test.runner in ("pytest", "jest", "vitest", "jasmine", "node"):
    path = test.id.split("::" if test.runner == "pytest" else SEP, 1)[0]
    return [path] if path in files else []
  if test.runner == "maven":
    cls = test.id.split("#", 1)[0].split("$", 1)[0]
    ends = [
      f"src/test/{lang}/{cls.replace('.', '/')}.{ext}"
      for lang, ext in (("java", "java"), ("kotlin", "kt"))
    ]
    return sorted(
      f for f in files if f.startswith(under) and any(_ends(f, e) for e in ends)
    )
  name = test.id.split("::", 1)[0].rsplit("\\", 1)[-1] + ".php"
  return sorted(
    f for f in files if f.startswith(under) and PurePosixPath(f).name == name
  )


def _ends(path: str, end: str) -> bool:
  return path == end or path.endswith("/" + end)

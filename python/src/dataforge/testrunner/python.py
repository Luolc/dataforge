"""The adapter of pytest.

An ID is a node ID relative to the project; restore_paths gives its file.
"""

from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import sys
import tomllib
from typing import final, override
import xml.etree.ElementTree as ET

from . import junit
from .adapter import Call, Context, PREFIX, rel, Runner, Spec, write_new
from .ctrf import Case
from .manifest import Test

RUNNER_FILES = (
  "conftest.py",
  "pytest.ini",
  ".pytest.ini",
  "pytest.toml",
  ".pytest.toml",
  "tox.ini",
  "sitecustomize.py",
  "usercustomize.py",
  "*.pth",
)
"""The files pytest, or the interpreter it runs in, loads by itself."""


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


SPECS = {
  "pytest": Spec(
    Pytest,
    runner_files=RUNNER_FILES,
    id_sep="::",
    files=("pytest_main.py", "junit.py"),
  )
}
"""pytest's Spec."""

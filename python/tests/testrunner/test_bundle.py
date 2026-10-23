import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import cast

import pytest

from dataforge.testrunner import core
from dataforge.testrunner.bundle import (
  COMMON,
  files,
  files_for,
  FORMAT_FILES,
  MAIN,
  registry,
)
from dataforge.testrunner.core import (
  build_config,
  FORMATS,
  id_file,
  load,
  RUNNERS,
  spec,
)
from dataforge.testrunner.registry import MODULES
from tests.testrunner.projects import write

PACKAGE = Path(core.__file__).parent
# The files only the host has.
HOST = {"bundle.py", MAIN}

# An ID of each runner, for id_file.
IDS = {
  "pytest": "tests/t.py::test_a",
  "jest": "src/a.test.js > a > x",
  "vitest": "src/a.test.ts > x",
  "jasmine": "spec/a_spec.js > a > x",
  "node": "test/a.test.js > x",
  "go": "example.com/m/p::TestA",
  "cargo": "tests/a.rs::x",
  "ctest": "parser",
  "gtest": "build/t::S.T",
  "maven": "a.B#c",
  "phpunit": "A\\B::c",
}

# Run by an interpreter that has only its standard library and a copy's
# tests/ on sys.path, as a task's is: prints what it found as JSON.
LOAD = """
import importlib.util
import json
import sys

sys.path.insert(0, sys.argv[1])
import testrunner
from testrunner import core, deps

runner, id_ = sys.argv[2], sys.argv[3]
print(json.dumps({
  "dataforge": importlib.util.find_spec("dataforge") is not None,
  "others": [p for p in sys.path[1:] if not p.startswith(sys.base_prefix)],
  "testrunner": testrunner.__file__,
  "adapter": core.load_adapter(runner).__name__,
  "build_config": list(core.build_config(runner)),
  "id_file": core.id_file(runner, id_),
}))
"""
# Runs a task's tests as a dataset's own harness does, from a copy's tests/.
HARNESS = """
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from testrunner import core

tests, app, out = (Path(a) for a in sys.argv[1:])
found = core.run(core.read(tests), tests=tests, app=app)
core.write(core.report(found.tests, 0, 0, unlisted=found.unlisted), out)
"""
DEPS = """
import sys

sys.path.insert(0, sys.argv[1])
from testrunner import deps

print(deps.dependency_overrides("cargo", sys.argv[2], sys.argv[3]))
"""

FAKE_JASMINE = """#!/usr/bin/env node
// Stands in for Jasmine: loads the reporter it is given and reports one spec.
const given = process.argv.find((a) => a.startsWith("--reporter="));
const Reporter = require(given.slice("--reporter=".length));
const r = new Reporter();
r.suiteStarted({ id: "s1", description: "a" });
r.specDone({ description: "x", status: "passed", failedExpectations: [] });
r.suiteDone({ id: "s1", failedExpectations: [] });
r.jasmineDone({ failedExpectations: [] });
"""
JUNIT = (
  '<testsuite><testcase classname="Math" name="adds"/>'
  '<testcase classname="Math" name="off"><failure message="1 != 2"/>'
  "</testcase></testsuite>"
)
OWN_GROUP = """
[[run]]
name = "own"
run = ["sh", "-c", "cp \\"$1\\" \\"$TESTRUNNER_OUT/\\"", "sh", "report.xml"]
parse = "junit"
"""
# For each end-to-end case: the project, the manifest, the files under
# tests/ beside it, and what the report lists.
RUNS: dict[
  str, tuple[dict[str, str], str, dict[str, str], list[tuple[str, str]]]
] = {
  "pytest": (
    {"t.py": "def test_a():\n  pass\n\n\ndef test_b():\n  assert False\n"},
    "".join(
      f'[[test]]\nsuite = "p2p"\nrunner = "pytest"\nid = "t.py::{t}"\n'
      for t in ("test_a", "test_b")
    ),
    {},
    [("t.py::test_a", "passed"), ("t.py::test_b", "failed")],
  ),
  "jasmine": (
    {"spec/a_spec.js": "", "node_modules/.bin/jasmine": FAKE_JASMINE},
    '[[test]]\nsuite = "p2p"\nrunner = "jasmine"\n'
    + 'id = "spec/a_spec.js > a > x"\n',
    {},
    [("spec/a_spec.js > a > x", "passed")],
  ),
  "junit": (
    {},
    OWN_GROUP
    + "".join(
      f'[[test]]\nsuite = "p2p"\nrun = "own"\nid = "Math::{t}"\n'
      for t in ("adds", "off")
    ),
    {"report.xml": JUNIT},
    [("Math::adds", "passed"), ("Math::off", "failed")],
  ),
}

# What the check of the common files lets through, and why:
# - core.py's _DROPPED_ENV, which keeps the caller's environment from
#   changing what every runner and run group runs, a group whose own argv
#   runs pytest among them, so it is no one runner's;
# - deps.py, the dependency overrides check, which a dataset runs on each
#   go.mod and Cargo.toml a fix changes or adds, whatever the task's runner,
#   so every task needs it.
EXEMPT_LINES = {
  "core.py": ['_DROPPED_ENV = ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")'],
}
EXEMPT_FILES = {"deps.py"}
LANGUAGE_FILES = (
  "pom.xml",
  "CMakeLists.txt",
  ".cmake",
  "go.mod",
  "Cargo.toml",
  "package.json",
  "pyproject.toml",
  "conftest.py",
  "node_modules",
  "phpunit.xml",
)


def _copy(tmp_path: Path, copied: dict[str, bytes]) -> Path:
  tests = tmp_path / "tests"
  for name, data in copied.items():
    path = tests / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(data)
  return tests


def _manifest(runner: str) -> str:
  return f"[[test]]\nsuite = 'p2p'\nrunner = '{runner}'\nid = '{IDS[runner]}'\n"


def _python(code: str, *args: str) -> str:
  # -I -S: no site-packages, so no installed dataforge, and no PYTHONPATH.
  done = subprocess.run(
    [sys.executable, "-I", "-S", "-c", code, *args],
    capture_output=True,
    text=True,
    check=True,
    env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
  )
  return done.stdout


def _words(text: str) -> set[str]:
  return {
    w.lower() for w in cast("list[str]", re.findall(r"[A-Za-z0-9]+", text))
  }


def _code_words(text: str) -> set[str]:
  # The words of a module's names and strings, its docstrings left out.
  tree = ast.parse(text)
  docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
  words: set[str] = set()
  for node in ast.walk(tree):
    if id(node) in docs:
      continue
    if isinstance(node, ast.Name):
      words |= _words(node.id)
    elif isinstance(node, ast.Attribute):
      words |= _words(node.attr)
    elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
      words |= _words(node.name)
    elif isinstance(node, ast.arg):
      words |= _words(node.arg)
    elif isinstance(node, ast.alias):
      words |= _words(node.name) | _words(node.asname or "")
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
      words |= _words(node.value)
  return words


def _modules(copied: dict[str, bytes]) -> dict[str, str]:
  # The MODULES of a copy's registry.py.
  tree = ast.parse(copied["testrunner/registry.py"])
  for node in tree.body:
    if isinstance(node, ast.AnnAssign) and ast.unparse(node.target) == (
      "MODULES"
    ):
      assert node.value is not None
      return cast("dict[str, str]", ast.literal_eval(node.value))
  raise AssertionError("registry.py has no MODULES")


def _common_source(copied: dict[str, bytes], name: str) -> str:
  # A common file's source in a copy, its exemptions cut out.
  text = copied[f"testrunner/{name}"].decode()
  for line in EXEMPT_LINES.get(name, []):
    assert line in text
    text = text.replace(line, "")
  return text


def test_maven_gets_no_other_runners_files():
  copied = files_for(load(_manifest("maven")))
  assert set(copied) == {
    f"testrunner/{n}"
    for n in (
      "__init__.py",
      "core.py",
      "deps.py",
      "registry.py",
      "junit.py",
      "maven.py",
    )
  }
  assert _modules(copied) == {"maven": "maven"}


def test_two_runners_get_both_their_files():
  both = files({"maven", "jasmine"}, ())
  maven, jasmine = files({"maven"}, ()), files({"jasmine"}, ())
  assert set(both) == set(maven) | set(jasmine)
  assert "testrunner/jasmine_reporter.cjs" in both
  assert "testrunner/junit.py" in both
  assert _modules(both) == _modules(maven) | _modules(jasmine)
  assert _modules(both) == {"jasmine": "js", "maven": "maven"}


def test_a_run_group_gets_the_files_of_its_parse_format():
  junit = files_for(load(OWN_GROUP))
  common = {"__init__.py", "core.py", "deps.py", "registry.py"}
  assert set(junit) == {f"testrunner/{n}" for n in (*common, "junit.py")}
  assert _modules(junit) == {}
  argv = OWN_GROUP.replace('parse = "junit"', 'parse = ["sh", "parse.sh"]')
  for other in (OWN_GROUP.replace("junit", "ctrf"), argv):
    assert set(files_for(load(other))) == {f"testrunner/{n}" for n in common}


def test_the_host_registry_names_every_runner():
  # A task's registry names only its runners; the host's names them all.
  assert _modules(files(RUNNERS, ())) == MODULES
  host = (PACKAGE / "registry.py").read_bytes()
  assert registry(RUNNERS) == host


# A manifest of each runner, and of a run group of each format.
MANIFESTS = {
  **{r: _manifest(r) for r in RUNNERS},
  "junit": OWN_GROUP,
  "ctrf": OWN_GROUP.replace("junit", "ctrf"),
}


@pytest.mark.parametrize("case", MANIFESTS)
def test_no_tasks_copy_has_the_hosts_files(case: str):
  copied = files_for(load(MANIFESTS[case]))
  assert not {f"testrunner/{n}" for n in HOST} & set(copied)


def test_main_is_copied_only_when_asked_for():
  plain = files({"maven"}, ("junit",))
  main = files({"maven"}, ("junit",), main=True)
  assert set(main) - set(plain) == {f"testrunner/{MAIN}"}
  assert "testrunner/bundle.py" not in main


@pytest.mark.parametrize(
  ("runners", "formats", "unknown"),
  [({"pytest", "rspec"}, (), "rspec"), ((), {"tap"}, "tap")],
)
def test_an_unknown_runner_or_format_is_refused(
  runners: set[str], formats: set[str], unknown: str
):
  with pytest.raises(
    ValueError, match=f"unknown runners or formats: {unknown}"
  ):
    _ = files(runners, formats)


def test_every_file_of_the_package_is_common_or_a_runners_or_a_formats():
  needed = {*COMMON, *(n for f in FORMATS for n in FORMAT_FILES[f])}
  for runner in RUNNERS:
    needed |= {f"{MODULES[runner]}.py", *spec(runner).files}
  package = {
    p.relative_to(PACKAGE).as_posix()
    for p in PACKAGE.rglob("*")
    if p.suffix in (".py", ".cjs") and "__pycache__" not in p.parts
  }
  assert package == needed | HOST
  assert not needed & HOST
  assert set(FORMAT_FILES) == set(FORMATS)
  assert set(files(RUNNERS, FORMATS)) == {f"testrunner/{n}" for n in needed}


def test_the_common_files_name_no_runner_or_language_file():
  # A copy with no runner and with MAIN: what every task's copy has, and
  # MAIN. go and node are English words too, so only code may not hold them.
  copied = files((), (), main=True)
  prose = set(RUNNERS) - {"go", "node"}
  for name in sorted({*COMMON, MAIN} - EXEMPT_FILES):
    text = _common_source(copied, name)
    assert not _words(text) & prose, name
    assert not _code_words(text) & {"go", "node"}, name
    assert not [f for f in LANGUAGE_FILES if f in text], name


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_copy_whose_registry_lacks_its_runner_cannot_load_it(
  tmp_path: Path, runner: str
):
  copied = files_for(load(_manifest(runner)))
  others = set(RUNNERS) - {runner}
  copied["testrunner/registry.py"] = registry(others)
  tests = _copy(tmp_path, copied)
  with pytest.raises(subprocess.CalledProcessError) as e:
    _ = _python(LOAD, str(tests), runner, IDS[runner])
  stderr = cast("str", e.value.stderr)
  assert f"ValueError: unknown runner: {runner}" in stderr


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_runners_files_alone_load_the_run_its_adapter_and_its_data(
  tmp_path: Path, runner: str
):
  tests = _copy(tmp_path, files_for(load(_manifest(runner))))
  found = cast(
    "dict[str, object]",
    json.loads(_python(LOAD, str(tests), runner, IDS[runner])),
  )
  assert found == {
    "dataforge": False,
    "others": [],
    "testrunner": str(tests / "testrunner" / "__init__.py"),
    "adapter": spec(runner).adapter.__name__,
    "build_config": list(build_config(runner)),
    "id_file": id_file(runner, IDS[runner]),
  }


def test_the_dependency_overrides_check_runs_in_any_tasks_copy(
  tmp_path: Path,
):
  # A pytest task's project may hold a Cargo.toml, such as a native
  # extension's: its copy, with no runner's module but pytest's, still
  # finds a dependency the fix takes from a path.
  tests = _copy(tmp_path, files_for(load(_manifest("pytest"))))
  assert not (tests / "testrunner" / "cargo.py").exists()
  base = '[dependencies]\nserde = "1"\n'
  text = '[dependencies]\nserde = { path = "../serde" }\n'
  found = _python(DEPS, str(tests), base, text)
  assert found == """['dependencies.serde = {"path": "../serde"}']\n"""


@pytest.mark.parametrize("case", RUNS)
def test_a_tasks_files_alone_run_its_tests(tmp_path: Path, case: str):
  # pytest and Jasmine need a file beyond their module; a run group with a
  # command of its own needs its format's. Jasmine's stand-in runs on node.
  assert shutil.which("node") is not None, "the tests need node on the PATH"
  project, manifest, beside, listed = RUNS[case]
  tests = _copy(tmp_path, files_for(load(manifest)))
  app, out = tmp_path / "app", tmp_path / core.FILE
  app.mkdir()
  write(app, project)
  for path in (app / "node_modules" / ".bin").glob("*"):
    path.chmod(0o755)
  write(tests, {"manifest.toml": manifest, **beside})
  _ = _python(HARNESS, str(tests), str(app), str(out))
  report = cast("dict[str, object]", json.loads(out.read_text()))
  found = cast(
    "list[dict[str, object]]",
    cast("dict[str, object]", report["results"])["tests"],
  )
  assert [(t["name"], t["status"], t.get("rawStatus")) for t in found] == [
    (i, s, None) for i, s in listed
  ]

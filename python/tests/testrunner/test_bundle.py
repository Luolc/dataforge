import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import cast

import pytest

from dataforge.testrunner import ctrf, files
from dataforge.testrunner.bundle import COMMON, NEEDS
from dataforge.testrunner.manifest import RUNNERS
from dataforge.testrunner.runners import ADAPTERS
from tests.testrunner.projects import write

PACKAGE = Path(ctrf.__file__).parent

# Run in a copy's tests/ by an interpreter that has only its standard
# library and that copy on sys.path, as a task's is: it prints what it
# found as JSON.
LOAD = """
import importlib.util
import json
import sys

sys.path.insert(0, sys.argv[1])
import testrunner.restore
import testrunner.run
from testrunner.runners import load_adapter

print(json.dumps({
  "dataforge": importlib.util.find_spec("dataforge") is not None,
  "others": [p for p in sys.path[1:] if not p.startswith(sys.base_prefix)],
  "testrunner": testrunner.__file__,
  "adapter": load_adapter(sys.argv[2]).__name__,
}))
"""
MAIN = """
import sys

sys.path.insert(0, sys.argv[1])
from testrunner.__main__ import main

main(sys.argv[2:])
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
PROJECTS = {
  "pytest": {
    "t.py": "def test_a():\n  pass\n\n\ndef test_b():\n  assert False\n"
  },
  "jasmine": {
    "spec/a_spec.js": "",
    "node_modules/.bin/jasmine": FAKE_JASMINE,
  },
}
LISTED = {
  "pytest": [("t.py::test_a", "passed"), ("t.py::test_b", "failed")],
  "jasmine": [("spec/a_spec.js > a > x", "passed")],
}


def _copy(tmp_path: Path, runners: set[str]) -> Path:
  # The tests/ of a task whose manifest names runners.
  tests = tmp_path / "tests"
  for name, data in files(runners).items():
    path = tests / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(data)
  return tests


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


def test_maven_gets_no_other_runners_files():
  found = set(files({"maven"}))
  assert found == {f"testrunner/{n}" for n in COMMON}
  for name in ("cargo.py", "cpp.py", "gotest.py", "js.py"):
    assert f"testrunner/{name}" not in found
  assert "testrunner/jasmine_reporter.cjs" not in found


def test_two_runners_get_both_their_files():
  both = files({"maven", "jasmine"})
  assert both == files({"maven"}) | files({"jasmine"})
  assert "testrunner/jasmine_reporter.cjs" in both
  assert "testrunner/js.py" in both


def test_an_unknown_runner_is_refused():
  with pytest.raises(ValueError, match="unknown runners: rspec"):
    _ = files({"pytest", "rspec"})


def test_every_file_of_the_package_is_common_or_a_runners():
  assert set(NEEDS) == set(RUNNERS)
  package = {
    p.relative_to(PACKAGE).as_posix()
    for p in PACKAGE.rglob("*")
    if p.suffix in (".py", ".cjs") and "__pycache__" not in p.parts
  }
  assert package == {*COMMON, *(n for r in RUNNERS for n in NEEDS[r])}
  assert set(files(RUNNERS)) == {f"testrunner/{n}" for n in package}


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_runners_files_alone_load_the_run_and_its_adapter(
  tmp_path: Path, runner: str
):
  tests = _copy(tmp_path, {runner})
  found = cast(
    "dict[str, object]", json.loads(_python(LOAD, str(tests), runner))
  )
  assert found == {
    "dataforge": False,
    "others": [],
    "testrunner": str(tests / "testrunner" / "__init__.py"),
    "adapter": ADAPTERS[runner][1],
  }


@pytest.mark.parametrize("runner", ["pytest", "jasmine"])
def test_a_runners_files_alone_run_its_tests(tmp_path: Path, runner: str):
  # The runners whose adapters need a file beyond their module; Jasmine's
  # stand-in runs on node.
  assert shutil.which("node") is not None, "the tests need node on the PATH"
  tests, app, out = (
    _copy(tmp_path, {runner}),
    tmp_path / "app",
    tmp_path / "out",
  )
  write(app, PROJECTS[runner])
  for path in (app / "node_modules" / ".bin").glob("*"):
    path.chmod(0o755)
  write(
    tests,
    {
      "manifest.toml": "".join(
        f'[[test]]\nsuite = "p2p"\nrunner = "{runner}"\nid = "{i}"\n\n'
        for i, _ in LISTED[runner]
      )
    },
  )
  args = ["--tests", str(tests), "--app", str(app), "--out", str(out)]
  _ = _python(MAIN, str(tests), *args)
  report = cast("dict[str, object]", json.loads((out / ctrf.FILE).read_text()))
  found = cast(
    "list[dict[str, object]]",
    cast("dict[str, object]", report["results"])["tests"],
  )
  assert [(t["name"], t["status"], t.get("rawStatus")) for t in found] == [
    (i, s, None) for i, s in LISTED[runner]
  ]

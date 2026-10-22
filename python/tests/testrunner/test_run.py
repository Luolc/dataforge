import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import cast, final, override

import pytest

from dataforge.testrunner import ctrf, files
from dataforge.testrunner.adapter import Call, Context, PREFIX, Runner
from dataforge.testrunner.ctrf import Case
from dataforge.testrunner.manifest import File, load
from dataforge.testrunner.manifest import Test as Listed
from dataforge.testrunner.run import match, place, run, run_group
from dataforge.testrunner.runners import Pytest
from tests.testrunner.projects import commit, context, write

BASE = {
  "pyproject.toml": (
    '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
    'addopts = "--strict-markers"\nmarkers = ["slow: a slow test"]\n'
  ),
  "src/parser/__init__.py": "def parse(text):\n  return text.split()\n",
  "conftest.py": (
    "import pytest\n\n\n@pytest.fixture\ndef words():\n  return 2\n"
  ),
  "tests/test_parse.py": """import pytest

from parser import parse


@pytest.mark.slow
def test_two(words):
  assert len(parse("a b")) == words


@pytest.mark.parametrize("text", ["a", "b c"])
def test_some(text):
  assert parse(text)


class TestEmpty:
  def test_none(self):
    assert parse("") == []


def test_unlisted():
  assert True
""",
}
MANIFEST = """
[[test]]
suite = "p2p"
runner = "pytest"
id = "tests/test_parse.py::test_two"

[[test]]
suite = "p2p"
runner = "pytest"
id = "tests/test_parse.py::test_some"

[[test]]
suite = "p2p"
runner = "pytest"
id = "tests/test_parse.py::TestEmpty"

[[test]]
suite = "p2p"
runner = "pytest"
id = "tests/test_parse.py::test_gone"

[[test]]
suite = "f2p"
runner = "pytest"
id = "tests/test_strict.py::test_strict"
kind = "vulnerability"

[[file]]
source = "test_strict.py"
dest = "tests/test_strict.py"
"""
F2P = """from parser import parse_strict


def test_strict():
  assert parse_strict
"""


def outcomes(found: list[ctrf.TestObject]) -> list[tuple[object, ...]]:
  return [
    (t["suite"], t["name"], t["status"], t.get("rawStatus"), t.get("message"))
    for t in found
  ]


def project(tmp_path: Path) -> tuple[Path, Path, str]:
  app, tests = tmp_path / "app", tmp_path / "tests"
  base = commit(app, BASE)
  write(tests, {"manifest.toml": MANIFEST, "test_strict.py": F2P})
  return app, tests, base


def test_run_runs_each_suite_with_the_bases_configuration(tmp_path: Path):
  app, tests, base = project(tmp_path)
  # A fix that turns the listed test off in the build file is not read.
  toml = app / "pyproject.toml"
  _ = toml.write_text(
    toml.read_text().replace("--strict-markers", "-k 'not two'")
  )
  found = run(load(MANIFEST), tests=tests, app=app, base=base)
  missing = f"{ctrf.MISSING} (the runner exited 0)"
  assert outcomes(found) == [
    (["p2p"], "tests/test_parse.py::test_two", "passed", None, None),
    (["p2p"], "tests/test_parse.py::test_some[a]", "passed", None, None),
    (["p2p"], "tests/test_parse.py::test_some[b c]", "passed", None, None),
    (
      ["p2p"],
      "tests/test_parse.py::TestEmpty::test_none",
      "passed",
      None,
      None,
    ),
    (["p2p"], "tests/test_parse.py::test_gone", "failed", "error", missing),
    (["f2p"], "tests/test_strict.py", "failed", "error", "collection failure"),
  ]
  assert found[-1]["labels"] == {
    "id": "f2p::tests/test_strict.py::test_strict",
    "runner": "pytest",
    "kind": "vulnerability",
  }
  assert (app / "tests" / "test_strict.py").read_text() == F2P
  assert not list(app.glob(f"{PREFIX}*"))


def test_a_case_belongs_to_the_longest_id_and_an_error_to_what_it_holds():
  tests = [
    Listed("p2p", "pytest", "t.py"),
    Listed("p2p", "pytest", "t.py::test_a"),
    Listed("p2p", "pytest", "u.py::test_b"),
    Listed("p2p", "pytest", "u.py::test_c"),
  ]
  cases = [
    Case("t.py::test_a", "passed"),
    Case("t.py::test_z", "failed"),
    Case("u.py", "failed", error=True, message="collection failure"),
    Case("v.py::test_d", "passed"),
  ]
  found = match(Pytest(), tests, cases)
  assert [
    (t["name"], cast("dict[str, str]", t["labels"])["id"]) for t in found
  ] == [
    ("t.py::test_z", "p2p::t.py"),
    ("t.py::test_a", "p2p::t.py::test_a"),
    ("u.py", "p2p::u.py::test_b"),
    ("u.py", "p2p::u.py::test_c"),
  ]


def test_a_runner_that_is_not_there_errs_each_test(tmp_path: Path):
  app, tests = tmp_path / "app", tmp_path / "tests"
  write(app, {"pom.xml": "<project/>"})
  tests.mkdir()
  manifest = load('[[test]]\nsuite = "p2p"\nrunner = "maven"\nid = "a.B#c"\n')
  path = os.environ["PATH"]
  try:
    os.environ["PATH"] = str(tmp_path / "nothing")
    found = run(manifest, tests=tests, app=app)
  finally:
    os.environ["PATH"] = path
  assert outcomes(found)[0][:4] == (["p2p"], "a.B#c", "failed", "error")
  assert "the runner did not start" in str(found[0]["message"])


def test_a_dir_outside_the_project_errs_its_tests(tmp_path: Path):
  app, tests = tmp_path / "app", tmp_path / "tests"
  write(tmp_path, {"outside/t.py": ""})
  app.mkdir()
  tests.mkdir()
  (app / "sub").symlink_to(tmp_path / "outside")
  manifest = load(
    '[[test]]\nsuite = "p2p"\nrunner = "pytest"\nid = "sub/t.py"\ndir = "sub"\n'
  )
  found = run(manifest, tests=tests, app=app)
  assert "its dir is not a directory of the project" in str(found[0]["message"])


def test_place_copies_over_a_link_without_following_it(tmp_path: Path):
  app, tests = tmp_path / "app", tmp_path / "tests"
  write(tests, {"t.py": "new"})
  write(app, {"a/keep.py": "kept"})
  (app / "a" / "t.py").symlink_to(app / "a" / "keep.py")
  place((File("t.py", "a/t.py"), File("t.py", "b/c/t.py")), tests, app)
  assert (app / "a" / "t.py").read_text() == "new"
  assert not (app / "a" / "t.py").is_symlink()
  assert (app / "a" / "keep.py").read_text() == "kept"
  assert (app / "b" / "c" / "t.py").read_text() == "new"


def test_place_refuses_a_link_on_the_way(tmp_path: Path):
  app, tests = tmp_path / "app", tmp_path / "tests"
  write(tests, {"t.py": "new"})
  app.mkdir()
  (tmp_path / "outside").mkdir()
  (app / "a").symlink_to(tmp_path / "outside")
  with pytest.raises(ValueError, match="a is not a directory"):
    place((File("t.py", "a/t.py"),), tests, app)
  assert not (tmp_path / "outside" / "t.py").exists()


# A stand-in for PHPUnit that reports one passed test at --log-junit.
FAKE_PHPUNIT = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in --log-junit=*) report="${arg#--log-junit=}" ;; esac
done
cat > "$report" <<'XML'
<testsuites><testsuite name="Example\\ParserTest">
<testcase name="testTag" class="Example\\ParserTest" time="0.002"/>
</testsuite></testsuites>
XML
"""
PY312_MANIFEST = """
[[test]]
suite = "p2p"
runner = "phpunit"
id = "Example\\\\ParserTest::testTag"

[[test]]
suite = "p2p"
runner = "pytest"
id = "t.py::test_a"
"""


def test_files_are_the_package_and_run_on_python_3_12(tmp_path: Path):
  # A task's Python may be 3.12. With -S nothing but its standard library can
  # be imported: a stand-in PHPUnit's report is read all the same, while
  # pytest cannot be imported, so its test is an error.
  python = shutil.which("python3.12")
  assert python is not None, "the tests need python3.12 on the PATH"
  tests, app, out = tmp_path / "tests", tmp_path / "app", tmp_path / "out"
  for name, data in files().items():
    path = tests / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(data)
  assert "testrunner/__main__.py" in files()
  assert "testrunner/jasmine_reporter.cjs" in files()
  assert not [n for n in files() if not n.startswith("testrunner/")]
  write(
    app,
    {
      "t.py": "def test_a():\n  pass\n",
      "tests/ParserTest.php": "",
      "vendor/bin/phpunit": FAKE_PHPUNIT,
    },
  )
  (app / "vendor/bin/phpunit").chmod(0o755)
  write(tests, {"manifest.toml": PY312_MANIFEST})
  args = ["--tests", str(tests), "--app", str(app), "--out", str(out)]
  done = subprocess.run(
    [python, "-S", "-m", "testrunner", *args],
    cwd=tests,
    capture_output=True,
    text=True,
    check=True,
    env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
  )
  assert json.loads(done.stdout.splitlines()[-1]) == {"p2p": [1, 2]}
  report = cast("dict[str, object]", json.loads((out / ctrf.FILE).read_text()))
  found = cast(
    "list[dict[str, object]]",
    cast("dict[str, object]", report["results"])["tests"],
  )
  assert [(t["name"], t["status"], t.get("rawStatus")) for t in found] == [
    ("Example\\ParserTest::testTag", "passed", None),
    ("t.py::test_a", "failed", "error"),
  ]


@final
class Merged(Runner):
  # An adapter whose one call's output is its report.

  @override
  def prepare(self, ctx: Context, tests: list[Listed]) -> list[Path]:
    return []

  @override
  def commands(self, ctx: Context, tests: list[Listed]) -> list[Call]:
    script = "echo out; echo err >&2"
    return [Call(["sh", "-c", script], output=ctx.work / "out.txt")]

  @override
  def cases(self, ctx: Context) -> list[Case] | None:
    text = (ctx.work / "out.txt").read_text()
    return [Case("t", "passed" if text == "out\nerr\n" else "failed")]

  @override
  def covers(self, outer: str, inner: str) -> bool:
    return outer == inner


def test_a_calls_output_is_its_report_and_is_shown(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
  found = run_group(Merged(), context(tmp_path), [Listed("p2p", "pytest", "t")])
  assert [(t["name"], t["status"]) for t in found] == [("t", "passed")]
  assert "out\nerr\n" in capsys.readouterr().out

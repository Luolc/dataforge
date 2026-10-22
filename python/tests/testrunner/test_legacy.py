"""A manifest of the format before run groups gives the report it gave then.

fixtures/legacy-ctrf.json is what `python -m dataforge.testrunner` wrote for
LEGACY_MANIFEST at the commit before run groups, its times set to 0 and the
project's path to <app>; every runner runs, the ones not on this machine as
stand-ins that write the reports in fixtures/.
"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import cast

from tests.testrunner.projects import write
from tests.testrunner.test_js import JEST_REPORT, NODE_TESTS

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "legacy-ctrf.json"

LEGACY_MANIFEST = """
[[test]]
suite = "p2p"
runner = "pytest"
id = "t/test_p.py::test_ok"

[[test]]
suite = "f2p"
runner = "pytest"
id = "t/test_p.py::test_fails"
kind = "vulnerability"
principle = "p1"

[[test]]
suite = "p2p"
runner = "pytest"
id = "t/test_p.py::test_some"

[[test]]
suite = "p2p"
runner = "pytest"
id = "t/test_p.py::test_skips"

[[test]]
suite = "p2p"
runner = "pytest"
id = "t/test_p.py::test_gone"

[[test]]
suite = "p2p"
runner = "node"
id = "test/a.test.js > attr"

[[test]]
suite = "p2p"
runner = "node"
id = "test/broken.test.js > loads"

[[test]]
suite = "p2p"
runner = "jest"
id = "src/attr.test.js > attr"

[[test]]
suite = "f2p"
runner = "jest"
id = "src/broken.test.js > x"

[[test]]
suite = "p2p"
runner = "vitest"
id = "src/attr.test.js > attr > nested"

[[test]]
suite = "p2p"
runner = "jasmine"
id = "spec/a_spec.js > a"

[[test]]
suite = "p2p"
runner = "go"
id = "example.com/proj/calc::TestAdd"

[[test]]
suite = "p2p"
runner = "go"
id = "example.com/proj/calc::TestSub"

[[test]]
suite = "f2p"
runner = "go"
id = "example.com/proj/broken::TestNew"

[[test]]
suite = "p2p"
runner = "cargo"
id = "crates/core/src/lib.rs::tests"
dir = "crates/core"

[[test]]
suite = "p2p"
runner = "cargo"
id = "crates/core/tests/parse.rs::parses"
dir = "crates/core"

[[test]]
suite = "p2p"
runner = "ctest"
id = "Math.Skips"
dir = "build"

[[test]]
suite = "f2p"
runner = "ctest"
id = "script_fail"
dir = "build"

[[test]]
suite = "p2p"
runner = "gtest"
id = "unit_tests::Math"
dir = "build"

[[test]]
suite = "f2p"
runner = "gtest"
id = "unit_tests::New.Added"
dir = "build"

[[test]]
suite = "p2p"
runner = "maven"
id = "com.example.ParserTest"
dir = "java"

[[test]]
suite = "p2p"
runner = "phpunit"
id = "Example\\\\ParserTest::testTag"
dir = "php"

[[file]]
source = "test_new.py"
dest = "t/test_new.py"
"""

PYTEST = """import pytest


def test_ok():
  assert True


def test_fails():
  assert 1 == 2, "one is not two"


@pytest.mark.parametrize("x", [1, 2])
def test_some(x):
  assert x == 1


@pytest.mark.skip(reason="not here")
def test_skips():
  pass
"""

JASMINE = {
  "specs": [
    {
      "titles": ["a", "x"],
      "status": "passed",
      "error": False,
      "duration": 4,
      "message": None,
      "trace": None,
    },
    {
      "titles": ["a", "y"],
      "status": "failed",
      "error": False,
      "duration": 1,
      "message": "Expected 1",
      "trace": "at y",
    },
  ],
  "errors": [],
}

SUREFIRE = """<testsuite name="com.example.ParserTest">
<testcase name="parses" classname="com.example.ParserTest" time="0.012"/>
<testcase name="fails" classname="com.example.ParserTest" time="0.003">
<failure message="expected 1">at ParserTest.java:9</failure></testcase>
</testsuite>
"""

PHPUNIT = """<testsuites><testsuite name="Example\\ParserTest">
<testcase name="testTag" class="Example\\ParserTest" time="0.002"/>
<testcase name="testOther" class="Example\\ParserTest" time="0.001">
<skipped/></testcase>
</testsuite></testsuites>
"""


def _script(path: Path, body: str) -> None:
  write(path.parent, {path.name: "#!/bin/sh\n" + body})
  path.chmod(0o755)


def _copy_to_arg(flag: str, source: Path) -> str:
  # A stand-in that copies source to the path given as flag<path>.
  return (
    'for arg in "$@"; do\n'
    f'  case "$arg" in {flag}*) out="${{arg#{flag}}}" ;; esac\n'
    "done\n"
    f'cp {shlex.quote(str(source))} "$out"\n'
  )


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
  app, tests = tmp_path / "app", tmp_path / "tests"
  stubs, data = tmp_path / "bin", tmp_path / "data"
  app.mkdir(parents=True)
  root = str(app.resolve())

  def render(name: str, text: str) -> Path:
    write(data, {name: text.replace("/w/app/", root + "/")})
    return data / name

  write(
    app,
    {
      "t/test_p.py": PYTEST,
      **NODE_TESTS,
      "src/attr.test.js": "",
      "src/broken.test.js": "",
      "spec/a_spec.js": "",
      "crates/core/src/lib.rs": "",
      "crates/core/tests/parse.rs": "",
      "java/pom.xml": "<project/>",
      "php/phpunit.xml": "<phpunit/>",
    },
  )
  write(tests, {"manifest.toml": LEGACY_MANIFEST, "test_new.py": PYTEST})
  jest = json.dumps(JEST_REPORT).replace("{app}", root)
  _script(
    app / "node_modules/.bin/jest",
    _copy_to_arg("--outputFile=", render("jest.json", jest)),
  )
  _script(
    app / "node_modules/.bin/vitest",
    _copy_to_arg("--outputFile.json=", render("vitest.json", jest)),
  )
  jasmine = render("jasmine.json", json.dumps(JASMINE))
  _script(
    app / "node_modules/.bin/jasmine",
    f'cp {shlex.quote(str(jasmine))} "$TESTRUNNER_JASMINE_REPORT"\n',
  )
  go = (FIXTURES / "go-test.jsonl").read_text()
  _script(stubs / "go", f"cat {shlex.quote(str(render('go.jsonl', go)))}\n")
  cargo = (FIXTURES / "cargo-test.txt").read_text().replace("/w/", root + "/")
  _script(stubs / "cargo", f"cat {render('cargo.txt', cargo)}\n")
  _script(stubs / "cmake", "exit 0\n")
  ctest = render("ctest.xml", (FIXTURES / "ctest.xml").read_text())
  _script(
    stubs / "ctest",
    f"""while [ $# -gt 0 ]; do
  if [ "$1" = --output-junit ]; then out="$2"; fi
  shift
done
cp {shlex.quote(str(ctest))} "$out"
""",
  )
  gtest = render("gtest.xml", (FIXTURES / "gtest.xml").read_text())
  _script(app / "build/unit_tests", _copy_to_arg("--gtest_output=xml:", gtest))
  surefire = render("surefire.xml", SUREFIRE)
  _script(
    stubs / "mvn",
    f"""mkdir -p target/surefire-reports
cp {shlex.quote(str(surefire))} target/surefire-reports/TEST-p.xml
""",
  )
  _script(
    app / "php/vendor/bin/phpunit",
    _copy_to_arg("--log-junit=", render("phpunit.xml", PHPUNIT)),
  )
  return app, tests, stubs


# Runs LEGACY_MANIFEST and returns the report and the counts, normalized.
def legacy_report(tmp_path: Path) -> str:
  app, tests, stubs = _project(tmp_path)
  out = tmp_path / "out"
  env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
  env["PATH"] = f"{stubs}{os.pathsep}{env['PATH']}"
  args = ["--tests", str(tests), "--app", str(app), "--out", str(out)]
  done = subprocess.run(
    [sys.executable, "-m", "dataforge.testrunner", *args],
    capture_output=True,
    text=True,
    check=True,
    env=env,
  )
  report = cast(
    "dict[str, dict[str, object]]", json.loads((out / "ctrf.json").read_text())
  )
  summary = cast("dict[str, int]", report["results"]["summary"])
  summary["start"] = summary["stop"] = 0
  for test in cast("list[dict[str, object]]", report["results"]["tests"]):
    test["duration"] = 0
  text = json.dumps(report, indent=2).replace(str(app.resolve()), "<app>")
  return f"{text}\n{done.stdout.splitlines()[-1]}\n"


def test_a_legacy_manifest_gives_the_report_it_gave_before(tmp_path: Path):
  assert legacy_report(tmp_path) == GOLDEN.read_text()

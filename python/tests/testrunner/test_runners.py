from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest

from dataforge.testrunner.adapter import Call, PREFIX
from dataforge.testrunner.ctrf import Case
from dataforge.testrunner.manifest import RUNNERS
from dataforge.testrunner.manifest import Test as Listed
from dataforge.testrunner.runners import (
  ADAPTERS,
  loaded_by_runner,
  Maven,
  PHPUnit,
  Pytest,
  restore_paths,
  RUNNER_FILES,
)
from tests.testrunner.projects import commit, context, write

PYTEST_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
<testcase classname="tests.test_parse" name="test_attr[a.b]"
  file="tests/test_parse.py" line="3" time="0.010"/>
<testcase classname="tests.test_parse.TestTag" name="test_close"
  file="tests/test_parse.py" time="0.002">
  <failure message="assert 1 == 2">def test_close(): ...</failure>
</testcase>
<testcase classname="tests.test_parse" name="test_skip"
  file="tests/test_parse.py" time="0"><skipped message="not here"/></testcase>
<testcase classname="" name="tests.test_new" file="tests/test_new.py" time="0">
  <error message="collection failure">ImportError: NEW</error>
</testcase>
</testsuite></testsuites>
"""

SUREFIRE_REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="org.example.ParserTest" tests="3">
  <testcase name="parsesAttr" classname="org.example.ParserTest" time="0.004"/>
  <testcase name="parsesTag(String)[1]" classname="org.example.ParserTest"
    time="0.001">
    <failure message="expected: 1 but was: 2">at x</failure>
  </testcase>
  <testcase name="" classname="org.example.ParserTest" time="0">
    <error message="setup failed" type="IllegalStateException">at y</error>
  </testcase>
</testsuite>
"""

PHPUNIT_REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites><testsuite name="Example\\Tests\\ParserTest">
  <testcase name="testAttr" file="{app}/tests/ParserTest.php" line="9"
    class="Example\\Tests\\ParserTest" classname="Example.Tests.ParserTest"
    assertions="1" time="0.003"/>
  <testcase name="testTag with data set #0" file="{app}/tests/ParserTest.php"
    class="Example\\Tests\\ParserTest" classname="Example.Tests.ParserTest"
    time="0.001"><skipped/></testcase>
  <testcase name="testTag with data set #1" file="/elsewhere/ParserTest.php"
    class="Example\\Tests\\ParserTest" classname="Example.Tests.ParserTest"
    time="0.001"><error type="Error">boom</error></testcase>
</testsuite></testsuites>
"""


def only(calls: list[Call]) -> list[str]:
  assert len(calls) == 1
  return calls[0].argv


def test_every_runner_has_an_adapter_and_its_files():
  assert set(RUNNERS) == set(ADAPTERS) == set(RUNNER_FILES)


def test_pytest_reads_node_ids_from_its_report(tmp_path: Path):
  ctx = context(tmp_path)
  write(ctx.app, {"tests/test_parse.py": "", "tests/test_new.py": ""})
  runner = Pytest()
  written = runner.prepare(
    ctx, [Listed("p2p", "pytest", "tests/test_parse.py")]
  )
  _ = (ctx.work / "pytest.xml").write_text(PYTEST_REPORT)
  assert runner.cases(ctx) == [
    Case(
      "tests/test_parse.py::test_attr[a.b]",
      "passed",
      10,
      file="tests/test_parse.py",
    ),
    Case(
      "tests/test_parse.py::TestTag::test_close",
      "failed",
      2,
      message="assert 1 == 2",
      trace="def test_close(): ...",
      file="tests/test_parse.py",
    ),
    Case(
      "tests/test_parse.py::test_skip",
      "skipped",
      message="not here",
      file="tests/test_parse.py",
    ),
    Case(
      "tests/test_new.py",
      "failed",
      error=True,
      message="collection failure",
      trace="ImportError: NEW",
      file="tests/test_new.py",
    ),
  ]
  assert written == [ctx.app / f"{PREFIX}pytest.ini"]


def test_pytest_has_no_cases_without_a_report(tmp_path: Path):
  ctx = context(tmp_path)
  runner = Pytest()
  _ = runner.prepare(ctx, [Listed("p2p", "pytest", "t.py")])
  assert runner.cases(ctx) is None


def test_pytest_takes_its_configuration_from_the_base(tmp_path: Path):
  base_toml = '[tool.pytest.ini_options]\naddopts = "-x"\n'
  ctx = context(tmp_path)
  base = commit(
    ctx.app, {"pkg/pyproject.toml": base_toml, "pkg/t/test_a.py": ""}
  )
  ctx = context(tmp_path, "pkg", base)
  _ = (ctx.dir / "pyproject.toml").write_text(
    '[tool.pytest.ini_options]\naddopts = "-k nothing"\n'
  )
  runner = Pytest()
  tests = [Listed("p2p", "pytest", "pkg/t/test_a.py::test_a", "pkg")]
  written = runner.prepare(ctx, tests)
  copy = ctx.dir / f"{PREFIX}pyproject.toml"
  assert written == [copy]
  assert copy.read_text() == base_toml
  argv = only(runner.commands(ctx, tests))
  assert argv[3:6] == ["t/test_a.py", f"-c={copy}", f"--rootdir={ctx.dir}"]


def test_pytest_takes_the_nearest_configuration_of_the_first_test(
  tmp_path: Path,
):
  ctx = context(tmp_path)
  base = commit(
    ctx.app,
    {
      "setup.cfg": "[metadata]\nname = x\n",
      "tox.ini": "[pytest]\naddopts = -q\n",
      "a/tox.ini": "[tox]\n",
      "a/setup.cfg": "[tool:pytest]\naddopts = -v\n",
      "a/test_a.py": "",
    },
  )
  ctx = context(tmp_path, base=base)
  runner = Pytest()
  tests = [Listed("p2p", "pytest", "a/test_a.py::test_a")]
  assert runner.prepare(ctx, tests) == [ctx.app / "a" / f"{PREFIX}setup.cfg"]
  argv = only(runner.commands(ctx, tests))
  assert f"--rootdir={ctx.app / 'a'}" in argv


def test_pytest_looks_from_the_common_ancestor_of_the_tests(tmp_path: Path):
  # As pytest does: a/pytest.ini is not on the way up from a and b's
  # common ancestor, the root, which has no configuration.
  ctx = context(tmp_path)
  files = {"a/pytest.ini": "[pytest]\n", "a/test_a.py": "", "b/test_b.py": ""}
  ctx = context(tmp_path, base=commit(ctx.app, files))
  tests = [
    Listed("p2p", "pytest", "a/test_a.py::test_one"),
    Listed("p2p", "pytest", "b/test_b.py::test_one"),
  ]
  runner = Pytest()
  assert runner.prepare(ctx, tests) == [ctx.app / f"{PREFIX}pytest.ini"]
  assert runner.prepare(ctx, tests[:1]) == [
    ctx.app / "a" / f"{PREFIX}pytest.ini"
  ]


def test_pytest_takes_a_file_with_a_dotted_directory_from_its_attribute(
  tmp_path: Path,
):
  ctx = context(tmp_path)
  write(ctx.app, {"v1.2/test_x.py": ""})
  runner = Pytest()
  _ = runner.prepare(ctx, [Listed("p2p", "pytest", "v1.2/test_x.py")])
  report = """<testsuites><testsuite>
<testcase classname="v1.2.test_x.TestK" name="test_m" file="v1.2/test_x.py"/>
</testsuite></testsuites>"""
  _ = (ctx.work / "pytest.xml").write_text(report)
  cases = runner.cases(ctx)
  assert cases is not None
  assert [c.name for c in cases] == ["v1.2/test_x.py::TestK::test_m"]


def test_pytest_runs_nothing_when_no_file_is_there(tmp_path: Path):
  ctx = context(tmp_path)
  runner = Pytest()
  tests = [Listed("p2p", "pytest", "gone.py::test_a")]
  _ = runner.prepare(ctx, tests)
  assert runner.commands(ctx, tests) == []


@pytest.mark.parametrize(
  ("outer", "inner", "covered"),
  [
    ("t.py::test_a", "t.py::test_a", True),
    ("t.py::test_a", "t.py::test_a[1]", True),
    ("t.py::TestK", "t.py::TestK::test_m", True),
    ("t.py", "t.py::test_a", True),
    ("tests", "tests/t.py::test_a", True),
    ("tests", "tests_more/t.py::test_a", False),
    ("t.py::test_a", "t.py::test_ab", False),
    ("t.py::test_a", "t.py", False),
  ],
)
def test_pytest_covers(outer: str, inner: str, covered: bool):
  assert Pytest().covers(outer, inner) is covered


def test_maven_reads_every_surefire_report_and_clears_old_ones(tmp_path: Path):
  ctx = context(tmp_path)
  reports = ctx.app / "core" / "target" / "surefire-reports"
  write(reports, {"TEST-old.xml": SUREFIRE_REPORT})
  runner = Maven()
  assert runner.prepare(ctx, []) == []
  assert runner.cases(ctx) is None
  write(reports, {"TEST-org.example.ParserTest.xml": SUREFIRE_REPORT})
  assert runner.cases(ctx) == [
    Case("org.example.ParserTest#parsesAttr", "passed", 4),
    Case(
      "org.example.ParserTest#parsesTag(String)[1]",
      "failed",
      1,
      message="expected: 1 but was: 2",
      trace="at x",
    ),
    Case(
      "org.example.ParserTest",
      "failed",
      error=True,
      message="setup failed",
      trace="at y",
    ),
  ]


def test_maven_names_the_tests_in_one_filter(tmp_path: Path):
  tests = [
    Listed("p2p", "maven", "org.example.ParserTest#parsesAttr"),
    Listed("p2p", "maven", "org.example.LexerTest"),
    Listed("p2p", "maven", "org.example.ParserTest#parsesTag"),
  ]
  argv = only(Maven().commands(context(tmp_path), tests))
  assert (
    "-Dtest=org.example.ParserTest#parsesAttr+parsesTag,org.example.LexerTest"
    in argv
  )
  assert argv[0] == "mvn" and argv[-1] == "test"
  # A class listed whole runs whole.
  tests.append(Listed("p2p", "maven", "org.example.ParserTest"))
  argv = only(Maven().commands(context(tmp_path), tests))
  assert "-Dtest=org.example.ParserTest,org.example.LexerTest" in argv


@pytest.mark.parametrize(
  ("outer", "inner", "covered"),
  [
    ("a.B#m", "a.B#m", True),
    ("a.B#m", "a.B#m(String)[1]", True),
    ("a.B", "a.B#m", True),
    ("a.B#m", "a.B#mm", False),
    ("a.B", "a.BC#m", False),
  ],
)
def test_maven_covers(outer: str, inner: str, covered: bool):
  assert Maven().covers(outer, inner) is covered


def test_phpunit_reads_its_report(tmp_path: Path):
  ctx = context(tmp_path)
  report = PHPUNIT_REPORT.replace("{app}", str(ctx.app))
  _ = (ctx.work / "phpunit.xml").write_text(report)
  cls = "Example\\Tests\\ParserTest"
  assert PHPUnit().cases(ctx) == [
    Case(f"{cls}::testAttr", "passed", 3, file="tests/ParserTest.php"),
    Case(
      f"{cls}::testTag with data set #0",
      "skipped",
      1,
      file="tests/ParserTest.php",
    ),
    Case(f"{cls}::testTag with data set #1", "failed", 1, True, trace="boom"),
  ]


def test_phpunit_filters_the_tests_by_name(tmp_path: Path):
  ctx = context(tmp_path)
  write(ctx.app, {"phpunit.xml.dist": "<phpunit/>"})
  tests = [
    Listed("p2p", "phpunit", "Example\\Tests\\ParserTest::testTag"),
    Listed("p2p", "phpunit", "Example\\Tests\\LexerTest"),
  ]
  argv = only(PHPUnit().commands(ctx, tests))
  assert argv[:2] == [
    "phpunit",
    f"--configuration={ctx.app / 'phpunit.xml.dist'}",
  ]
  pattern = argv[-1].removeprefix("--filter=/").removesuffix("/")
  # The pattern uses nothing PCRE and Python's re read differently.
  for name, matched in (
    ("Example\\Tests\\ParserTest::testTag", True),
    ("Example\\Tests\\ParserTest::testTag with data set #0", True),
    ("Example\\Tests\\LexerTest::testAny", True),
    ("Example\\Tests\\ParserTest::testTagged", False),
    ("Example\\Tests\\ParserTest::testAttr", False),
    ("Example\\Tests\\LexerTestCase::testAny", False),
  ):
    assert bool(re.search(pattern, name)) is matched, name


def test_phpunit_without_a_configuration_runs_the_listed_classes_files(
  tmp_path: Path,
):
  # The run's own configuration names the files of the listed classes, none
  # under vendor/.
  ctx = context(tmp_path)
  write(
    ctx.app,
    {
      "vendor/bin/phpunit": "",
      "vendor/x/tests/ParserTest.php": "",
      "tests/Unit/ParserTest.php": "",
      "tests/Unit/LexerTest.php": "",
      "tests/Feature/LexerTest.php": "",
    },
  )
  tests = [
    Listed("p2p", "phpunit", "App\\Tests\\ParserTest::testTag"),
    Listed("p2p", "phpunit", "App\\Tests\\LexerTest"),
  ]
  runner = PHPUnit()
  assert runner.prepare(ctx, tests) == []
  config = ctx.work / "phpunit-tests.xml"
  argv = only(runner.commands(ctx, tests))
  assert argv[:2] == [
    str(ctx.app / "vendor/bin/phpunit"),
    f"--configuration={config}",
  ]
  suite = ET.parse(config).getroot().find("testsuites/testsuite")
  assert suite is not None
  assert [f.text for f in suite.iter("file")] == [
    str(ctx.app / "tests/Feature/LexerTest.php"),
    str(ctx.app / "tests/Unit/LexerTest.php"),
    str(ctx.app / "tests/Unit/ParserTest.php"),
  ]
  gone = context(tmp_path / "gone")
  write(gone.app, {"tests/ParserTest.php": ""})
  missing = [Listed("p2p", "phpunit", "A\\GoneTest")]
  assert runner.prepare(gone, missing) == []
  assert runner.commands(gone, missing) == []


@pytest.mark.parametrize(
  ("runner", "path", "loaded"),
  [
    ("pytest", "conftest.py", True),
    ("pytest", "src/pkg/conftest.py", True),
    ("pytest", "site-packages/x.pth", True),
    ("pytest", "pyproject.toml", False),
    ("maven", ".mvn/maven.config", True),
    ("maven", "core/.mvn/jvm.config", True),
    ("maven", "maven.config", False),
    ("maven", "pom.xml", False),
    ("phpunit", "phpunit.xml.dist", True),
    ("phpunit", "composer.json", False),
  ],
)
def test_loaded_by_runner(runner: str, path: str, loaded: bool):
  assert loaded_by_runner(runner, path) is loaded


BASE_FILES = {
  "tests/test_parse.py",
  "core/src/test/java/org/example/ParserTest.java",
  "core/src/main/java/org/example/ParserTest.java",
  "web/src/test/kotlin/org/example/ParserTest.kt",
  "php/tests/Unit/ParserTest.php",
  "php/src/Parser.php",
}


@pytest.mark.parametrize(
  ("test", "paths"),
  [
    (
      Listed("p2p", "pytest", "tests/test_parse.py::test_a"),
      ["tests/test_parse.py"],
    ),
    (Listed("p2p", "pytest", "tests/gone.py::test_a"), []),
    (
      Listed("p2p", "maven", "org.example.ParserTest$Inner#m"),
      [
        "core/src/test/java/org/example/ParserTest.java",
        "web/src/test/kotlin/org/example/ParserTest.kt",
      ],
    ),
    (
      Listed("p2p", "maven", "org.example.ParserTest", "core"),
      ["core/src/test/java/org/example/ParserTest.java"],
    ),
    (
      Listed("p2p", "phpunit", "Example\\Tests\\ParserTest::testTag", "php"),
      ["php/tests/Unit/ParserTest.php"],
    ),
  ],
)
def test_restore_paths(test: Listed, paths: list[str]):
  assert restore_paths(test, BASE_FILES) == paths

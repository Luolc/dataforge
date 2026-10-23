from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest

from dataforge.testrunner import cpp, junit
from dataforge.testrunner.core import Call, Context, restore_paths
from dataforge.testrunner.core import Test as Listed
from dataforge.testrunner.cpp import CTest, GTest
from tests.testrunner.projects import context


def test_ctest_configures_builds_and_runs_the_listed_names(tmp_path: Path):
  ctx = context(tmp_path, "build")
  tests = [
    Listed("p2p", "ctest", "Math.Adds", "build"),
    Listed("p2p", "ctest", "Inst/P.Even/0", "build"),
  ]
  report = ctx.work / "ctest.xml"
  assert CTest().commands(ctx, tests) == [
    Call(
      [
        "sh",
        "-c",
        " ".join(
          [
            "cmake . && cmake --build . && exec ctest",
            f"--output-on-failure --output-junit {report}",
            "-R '^(Math\\.Adds|Inst/P\\.Even/0)$'",
          ]
        ),
      ]
    )
  ]


@pytest.mark.parametrize(
  ("name", "other"),
  [("Math.Adds", "MathXAdds"), ("a+b (x)", "aab (x)"), ("[x]|y", "x")],
)
def test_ctests_pattern_matches_only_the_name(name: str, other: str):
  # CMake's syntax is a subset of Python's for these characters.
  pattern = cpp._cmake_regex(name)  # pyright: ignore[reportPrivateUsage]
  assert re.fullmatch(pattern, name)
  assert not re.fullmatch(pattern, other)


FILTER = ["Math.Adds", "Math.Adds/*", "Inst/P.Even", "Inst/P.Even/*", "New.*"]


def test_gtest_builds_and_runs_each_binary_with_its_filter(tmp_path: Path):
  ctx = context(tmp_path, "build")
  tests = [
    Listed("p2p", "gtest", "unit_tests::Math.Adds", "build"),
    Listed("p2p", "gtest", "unit_tests::Inst/P.Even", "build"),
    Listed("p2p", "gtest", "unit_tests::New", "build"),
    Listed("p2p", "gtest", "bin/other_tests", "build"),
  ]
  first, second = ctx.work / "gtest-0.xml", ctx.work / "gtest-1.xml"
  assert GTest().commands(ctx, tests) == [
    Call(
      [
        "sh",
        "-c",
        " ".join(
          [
            "cmake . && cmake --build . --target unit_tests && exec",
            f"./unit_tests --gtest_output=xml:{first}",
            "'--gtest_filter=" + ":".join(FILTER) + "'",
          ]
        ),
      ]
    ),
    Call(
      [
        "sh",
        "-c",
        " ".join(
          [
            "cmake . && cmake --build . --target other_tests && exec",
            f"./bin/other_tests --gtest_output=xml:{second}",
          ]
        ),
      ]
    ),
  ]


@pytest.mark.parametrize(
  ("outer", "inner", "covered"),
  [
    ("b::Math.Adds", "b::Math.Adds", True),
    ("b::Math", "b::Math.Adds", True),
    ("b::Inst/P.Even", "b::Inst/P.Even/1", True),
    ("b", "b::Math.Adds", True),
    ("b::Math", "b::MathX.Adds", False),
    ("b::Math.Add", "b::Math.Adds", False),
    ("b", "bx::Math.Adds", False),
  ],
)
def test_gtest_covers(outer: str, inner: str, covered: bool):
  assert cpp.covers(outer, inner) is covered


@pytest.mark.parametrize(
  ("attributes", "status"),
  [
    ({"status": "run", "result": "completed"}, "passed"),
    ({"status": "run", "result": "skipped"}, "skipped"),
    ({"status": "notrun"}, "skipped"),
    ({"status": "disabled"}, "skipped"),
  ],
)
def test_a_test_not_run_or_skipped_by_attribute_is_skipped(
  attributes: dict[str, str], status: str
):
  case = ET.Element("testcase", {"name": "t", **attributes})
  assert junit.marked_outcome(case).status == status


def test_cpp_tests_restore_nothing():
  files = ["tests/math_test.cc"]
  for runner, id_ in (("ctest", "Math.Adds"), ("gtest", "unit_tests::Math")):
    assert restore_paths(Listed("p2p", runner, id_, "build"), files) == []


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str, ctx: Context, dest: Path) -> None:
  # A report of CMake 3.28's CTest or GoogleTest 1.14, run in /w.
  text = (FIXTURES / name).read_text().replace("/w/app/", f"{ctx.app}/")
  _ = dest.write_text(text)


def test_ctest_reads_its_junit_with_the_tests_output(tmp_path: Path):
  ctx = context(tmp_path, "build")
  fixture("ctest.xml", ctx, ctx.work / "ctest.xml")
  found = [
    (c.name, c.status, c.message, (c.trace or "").splitlines()[-1:])
    for c in CTest().cases(ctx) or []
  ]
  assert found == [
    ("Math.Fails", "failed", "Failed", [" 1 FAILED TEST"]),
    (
      "Math.Skips",
      "skipped",
      "SKIP_REGULAR_EXPRESSION_MATCHED",
      ["[  SKIPPED ] Math.Skips"],
    ),
    ("whole", "failed", "Failed", ["  YOU HAVE 1 DISABLED TEST"]),
    ("script_fail", "failed", "Failed", []),
  ]


def test_gtest_reads_each_binarys_xml(tmp_path: Path):
  ctx = context(tmp_path, "build")
  runner = GTest()
  _ = runner.commands(ctx, [Listed("f2p", "gtest", "unit_tests", "build")])
  fixture("gtest.xml", ctx, ctx.work / "gtest-0.xml")
  found = [
    (c.name, c.status, (c.message or "").splitlines()[1:2], c.file)
    for c in runner.cases(ctx) or []
  ]
  math = "tests/math_test.cc"
  assert found == [
    ("unit_tests::Math.Adds", "passed", [], math),
    (
      "unit_tests::Math.Fails",
      "failed",
      ["Expected equality of these values:"],
      math,
    ),
    ("unit_tests::Math.Skips", "skipped", ["not here"], math),
    ("unit_tests::Math.DISABLED_Off", "skipped", [], math),
    ("unit_tests::Math.Unlisted", "passed", [], math),
    ("unit_tests::New.Added", "passed", [], "tests/new_test.cc"),
    ("unit_tests::Inst/P.Even/0", "passed", [], math),
    (
      "unit_tests::Inst/P.Even/1",
      "failed",
      ["Expected equality of these values:"],
      math,
    ),
  ]

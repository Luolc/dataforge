import json
from pathlib import Path

import pytest

from dataforge.testrunner import cargo, gotest
from dataforge.testrunner.cargo import Cargo
from dataforge.testrunner.core import Call, Case, restore_paths
from dataforge.testrunner.core import Test as Listed
from dataforge.testrunner.gotest import GoTest
from tests.testrunner.projects import context, write


@pytest.mark.parametrize(
  ("outer", "inner", "covered"),
  [
    ("m/calc::TestSub", "m/calc::TestSub", True),
    ("m/calc::TestSub", "m/calc::TestSub/two_words", True),
    ("m/calc", "m/calc::TestAdd", True),
    ("m/calc::TestSub", "m/calc::TestSubtract", False),
    ("m/calc", "m/calcx::TestAdd", False),
  ],
)
def test_go_covers(outer: str, inner: str, covered: bool):
  assert gotest.covers(outer, inner) is covered


def test_go_runs_the_listed_packages_top_level_tests(tmp_path: Path):
  ctx = context(tmp_path)
  tests = [
    Listed("p2p", "go", "m/calc::TestSub/two_words"),
    Listed("p2p", "go", "m/calc::TestAdd"),
    Listed("p2p", "go", "m/parse::TestAdd"),
  ]
  report = ctx.work / "go.json"
  assert GoTest().commands(ctx, tests) == [
    Call(
      [
        "go",
        "test",
        "-json",
        "-count=1",
        "-run=^(?:TestSub|TestAdd)$",
        "m/calc",
        "m/parse",
      ],
      output=report,
    )
  ]
  whole = [*tests, Listed("p2p", "go", "m/util")]
  argv = GoTest().commands(ctx, whole)[0].argv
  assert argv == [
    "go",
    "test",
    "-json",
    "-count=1",
    "m/calc",
    "m/parse",
    "m/util",
  ]


def test_go_restores_its_packages_test_files():
  files = [
    "svc/go.mod",
    "svc/internal/parse/parse.go",
    "svc/internal/parse/parse_test.go",
    "svc/internal/parse/more_test.go",
    "svc/internal/parse/deep/deep_test.go",
    "svc/parse/other_test.go",
  ]
  test = Listed("p2p", "go", "example.com/svc/internal/parse::TestA", dir="svc")
  assert restore_paths(test, files) == [
    "svc/internal/parse/more_test.go",
    "svc/internal/parse/parse_test.go",
  ]
  root = Listed("p2p", "go", "example.com/svc::TestA", dir="svc")
  assert restore_paths(root, files) == []


@pytest.mark.parametrize(
  ("source", "args"),
  [
    ("src/lib.rs", ["--lib"]),
    ("src/main.rs", ["--bins"]),
    ("src/bin/tool.rs", ["--bins"]),
    ("src/bin/tool/main.rs", ["--bins"]),
    ("tests/parse.rs", ["--test", "parse"]),
    ("tests/parse/main.rs", ["--test", "parse"]),
    ("src/parse.rs", None),
    ("tests/common/mod.rs", None),
    ("benches/b.rs", None),
  ],
)
def test_cargo_target(source: str, args: list[str] | None):
  assert cargo.target(source) == args


def core(id_: str) -> Listed:
  return Listed("p2p", "cargo", f"crates/core/{id_}", "crates/core")


def test_cargo_runs_the_listed_targets_with_their_names(tmp_path: Path):
  ctx = context(tmp_path, "crates/core")
  write(
    ctx.dir,
    {
      "src/lib.rs": "",
      "src/main.rs": "",
      "src/bin/tool.rs": "",
      "tests/parse.rs": "",
      "x.rs": "",
    },
  )
  tests = [
    core("src/lib.rs::tests::adds"),
    core("src/main.rs::tests"),
    core("tests/parse.rs::parses"),
    core("src/lib.rs::tests::fails"),
    core("tests/parse.rs::aborts"),
    core("src/bin/tool.rs::runs"),
  ]
  report = ctx.work / "cargo.txt"
  assert Cargo().commands(ctx, tests) == [
    Call(
      [
        *("cargo", "test", "--offline", "--no-fail-fast"),
        *("--lib", "--bins", "--test", "parse"),
        *("--", "tests::adds", "tests", "parses", "tests::fails"),
        *("aborts", "runs"),
      ],
      output=report,
    )
  ]
  whole = [*tests, core("tests/parse.rs")]
  assert "--" not in Cargo().commands(ctx, whole)[0].argv
  # A file that is no target's root, or not there, runs nothing.
  elsewhere = [core("x.rs::a"), core("tests/gone.rs::a")]
  assert Cargo().commands(ctx, elsewhere) == []


def test_cargo_restores_only_a_test_target_of_its_own():
  files = ["core/tests/parse.rs", "core/src/lib.rs", "core/tests/common/mod.rs"]
  ids = ["core/tests/parse.rs::parses", "core/src/lib.rs::tests::adds"]
  found = [restore_paths(Listed("p2p", "cargo", i, "core"), files) for i in ids]
  assert found == [["core/tests/parse.rs"], []]


FIXTURES = Path(__file__).parent / "fixtures"


def summary(cases: list[Case]) -> list[tuple[str, str, bool, str | None]]:
  return [(c.name, c.status, c.error, c.message) for c in cases]


def test_go_cases_read_go_tests_json():
  # go1.27.3's output for packages that pass, fail, skip, run subtests, do
  # not build, panic, exit in TestMain and are not there.
  cases = gotest.go_cases((FIXTURES / "go-test.jsonl").read_text())
  calc = "example.com/proj/calc::"
  assert summary(cases) == [
    (
      "example.com/proj/gone",
      "failed",
      True,
      "no required module provides package example.com/proj/gone; to add it:",
    ),
    (
      "example.com/proj/broken",
      "failed",
      True,
      "broken/broken_test.go:5:33: undefined: undefinedCall",
    ),
    (calc + "TestAdd", "passed", False, None),
    (calc + "TestFail", "failed", False, "add_test.go:11: got 2, want 3"),
    (calc + "TestSkip", "skipped", False, None),
    (calc + "TestSub", "failed", False, None),
    (calc + "TestSub/two_words", "passed", False, None),
    (calc + "TestSub/bad", "failed", False, "add_test.go:17: sub failed"),
    (
      "example.com/proj/panics::TestPanic",
      "failed",
      False,
      "panic: boom [recovered, repanicked]",
    ),
    ("example.com/proj/mainexit", "failed", True, "exit status 3"),
  ]


def test_cargo_cases_read_libtests_text(tmp_path: Path):
  # cargo 1.99.1's output for a lib's and a bin's unit tests and two test
  # targets, one of which aborts.
  ctx = context(tmp_path, "crates/core")
  text = (FIXTURES / "cargo-test.txt").read_text().replace("/w/", f"{ctx.app}/")
  core = "crates/core/"
  sources = [core + "src/lib.rs", core + "tests/parse.rs"]
  cases = cargo.cargo_cases(ctx, text, sources)
  assert summary(cases) == [
    (core + "src/lib.rs::tests::ignored", "skipped", False, None),
    (core + "src/lib.rs::tests::adds", "passed", False, None),
    (
      core + "src/lib.rs::tests::fails",
      "failed",
      False,
      "assertion `left == right` failed",
    ),
    (core + "src/lib.rs::tests::panics", "passed", False, None),
    (core + "src/main.rs::tests::runs_main", "passed", False, None),
    (core + "tests/other.rs::other_two", "failed", False, "other two failed"),
    (
      core + "tests/parse.rs",
      "failed",
      True,
      "its test binary ended with no result",
    ),
  ]


RUNNING = "     Running tests/a.rs (target/debug/deps/a-1)"


def result(passed: int) -> str:
  return f"test result: ok. {passed} passed; 0 failed; 0 ignored; 0 measured"


@pytest.mark.parametrize(
  ("lines", "names", "why", "times"),
  [
    # A line a test's code wrote past libtest's capture.
    (
      ["test one ... ok", "test never_runs ... ok", result(1)],
      {"one": "", "never_runs": ""},
      "its results do not match its binary's count of them",
      1,
    ),
    (
      ["test one ... ok", "test one ... ok", "test two ...", result(1)],
      {"one": "it was reported twice", "two": "it did not end"},
      "its results do not match its binary's count of them",
      1,
    ),
    (
      ["test one ... ok", result(1), RUNNING, "test one ... ok", result(1)],
      {"one": ""},
      "a test binary of its file ran twice",
      2,
    ),
    (
      ["test one ... ok"],
      {"one": ""},
      "its test binary ended with no result",
      1,
    ),
  ],
)
def test_cargo_cases_hold_a_binarys_results_only_when_it_counts_them(
  tmp_path: Path,
  lines: list[str],
  names: dict[str, str],
  why: str,
  times: int,
):
  ctx = context(tmp_path)
  found = summary(cargo.cargo_cases(ctx, "\n".join([RUNNING, *lines]), []))
  cases = [
    (f"tests/a.rs::{n}", "failed", True, m or why) for n, m in names.items()
  ]
  whole = ("tests/a.rs", "failed", True, why)
  assert found == [*cases, whole] * times


def test_cargo_cases_take_a_binary_that_counts_its_results(tmp_path: Path):
  ctx = context(tmp_path)
  text = "\n".join([RUNNING, "test one ... ok", "test two ... ok", result(2)])
  assert summary(cargo.cargo_cases(ctx, text, [])) == [
    ("tests/a.rs::one", "passed", False, None),
    ("tests/a.rs::two", "passed", False, None),
  ]


def test_cargo_cases_without_a_binary_error_each_listed_file(tmp_path: Path):
  ctx = context(tmp_path)
  text = "   Compiling a v0.1.0\nerror[E0425]: cannot find function `gone`\n"
  assert summary(cargo.cargo_cases(ctx, text, ["tests/a.rs"])) == [
    ("tests/a.rs", "failed", True, "error[E0425]: cannot find function `gone`"),
  ]


def test_go_cases_count_a_test_that_never_ends_as_an_error():
  hangs = {"Package": "m/p", "Test": "TestHangs"}
  events = [
    {"Action": "run", **hangs},
    {"Action": "output", **hangs, "Output": "=== RUN   TestHangs\n"},
    {"Action": "output", "Package": "m/p", "Output": "panic: test timed out\n"},
    {"Action": "fail", "Package": "m/p", "Elapsed": 600},
  ]
  text = "\n".join(json.dumps(e) for e in events)
  assert summary(gotest.go_cases(text)) == [
    ("m/p::TestHangs", "failed", True, "it did not end"),
    ("m/p", "failed", True, "panic: test timed out"),
  ]


def test_go_cases_count_a_test_that_ends_twice_as_an_error():
  test = {"Package": "m/p", "Test": "TestA"}
  events = [{"Action": a, **test} for a in ("run", "fail", "pass")]
  text = "\n".join(json.dumps(e) for e in events)
  assert summary(gotest.go_cases(text)) == [
    ("m/p::TestA", "failed", True, "it ended twice"),
  ]

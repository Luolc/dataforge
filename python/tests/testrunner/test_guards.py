import json
import os
from pathlib import Path
import subprocess
import sys
from typing import cast

import pytest

from dataforge.testrunner import cargo, core, deps
from dataforge.testrunner.bundle import (
  files,
  files_for,
  guards_for,
  guards_of,
  registry,
)
from dataforge.testrunner.core import load, Test
from dataforge.testrunner.registry import GUARDS
from tests.testrunner.projects import write

# Run by an interpreter that has only its standard library and a copy's
# tests/ on sys.path, as a task's is: prints, as JSON, what the copy's
# checks of a fix's files find for each path and the lines added to it.
CHECKS = """
import json
import sys

sys.path.insert(0, sys.argv[1])
from testrunner import core

cases = json.loads(sys.argv[2])
print(json.dumps([
  [core.is_test_file(path), core.rewires(path, added)]
  for path, added in cases
]))
"""


def _checks(
  tmp_path: Path, copied: dict[str, bytes], cases: list[tuple[str, str]]
) -> list[list[object]]:
  # -I -S: no site-packages, so no installed dataforge, and no PYTHONPATH.
  tests = tmp_path / "tests"
  for name, data in copied.items():
    path = tests / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(data)
  done = subprocess.run(
    [sys.executable, "-I", "-S", "-c", CHECKS, str(tests), json.dumps(cases)],
    capture_output=True,
    text=True,
    check=True,
    env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
  )
  return cast("list[list[object]]", json.loads(done.stdout))


@pytest.mark.parametrize(
  ("path", "test"),
  [
    ("src/parse.test.ts", True),
    ("lib/parse.spec.js", True),
    ("lib/parse_spec.js", True),
    ("spec/support/helpers.js", True),
    ("spec/support/helpers.rb", True),
    ("packages/core/spec/parse.js", True),
    ("src/__tests__/parse.js", True),
    ("module/src/test/java/org/x/ParseTest.java", True),
    ("app/src/test/kotlin/x/ParseTest.kt", True),
    ("tests/test_parse.py", True),
    ("pkg/parse_test.go", True),
    ("lib/conftest.py", True),
    ("src/parse.js", False),
    ("src/main/java/org/x/Parse.java", False),
    ("src/latest.js", False),
    ("types/parse.d.ts", False),
    ("litellm/proxy/common_utils/custom_openapi_spec.py", False),
    ("airflow-core/src/airflow/listeners/spec/lifecycle.py", False),
    ("vendor/github.com/onsi/ginkgo/internal/spec/spec.go", False),
    ("crates/x/src/spec/mod.rs", False),
    ("spec/assets/sample.xml", True),
  ],
)
def test_a_test_file_is_told_by_its_path(path: str, test: bool):
  assert core.is_test_file(path) is test


@pytest.mark.parametrize(
  ("path", "test"),
  [
    ("tests", True),
    ("Test", True),
    ("lib/tests", False),
    ("packages/core/spec", True),
    ("src/__tests__", True),
    ("module/src/test", True),
    ("src", False),
    ("", False),
  ],
)
def test_a_test_dir_is_told_by_its_path(path: str, test: bool):
  assert core.is_test_dir(path) is test


# A Rust source file that holds the code a repair changes and, below it,
# unit tests; what looks like a test module inside a comment, a string or
# a raw string is none, nor is one built only outside tests.
RUST_SOURCE = """\
pub fn verify(sig: &str) -> bool {
  // #[cfg(test)] mod fake { }
  let _s = "#[cfg(test)] mod fake {";
  let _r = r#"} #[cfg(test)] mod fake { "#;
  let _c = '{';
  !sig.is_empty()
}

#[cfg(not(test))]
mod real {}

#[cfg(test)]
mod helpers;

#[cfg(test)]
#[allow(unused)]
pub(crate) mod tests {
  use super::*;

  #[test]
  fn test_verify() {
    /* } nested /* } */ } */
    assert!(verify("x"));
  }
}

#[cfg(all(test, unix))]
mod unix_tests {
  #[test]
  fn test_unix() {}
}
"""


def test_the_inline_test_modules_of_a_rust_file_are_found():
  assert cargo.test_modules(RUST_SOURCE) == [(15, 25), (27, 31)]


def test_the_lines_of_listed_cargo_tests_are_kept(tmp_path: Path):
  # A test in src/lib.rs keeps its file's test modules; one in a test
  # target of its own is restored instead, and a test of another runner
  # keeps nothing.
  write(
    tmp_path,
    {
      "src/lib.rs": RUST_SOURCE,
      "src/helpers.rs": "pub fn h() {}\n",
      "tests/it.rs": "#[test]\nfn it() {}\n",
    },
  )
  tests = [
    Test("p2p", "cargo", "src/lib.rs::tests::test_verify"),
    Test("p2p", "cargo", "src/lib.rs::helpers::h"),
    Test("p2p", "cargo", "tests/it.rs::it"),
    Test("p2p", "pytest", "src/lib.rs::x"),
  ]
  assert core.kept_lines(tmp_path, tests) == {
    "src/lib.rs": [(15, 25), (27, 31), (17, 25)],
    "src/helpers.rs": [cargo.WHOLE],
  }


@pytest.mark.parametrize(
  ("path", "added", "found"),
  [
    ("src/lib.rs", "macro_rules! assert_eq {", "macro_rules! assert_eq"),
    (
      "src/lib.rs",
      "macro_rules!\n  r#panic { () => {} }",
      "macro_rules! panic",
    ),
    ("src/lib.rs", '#[cfg_attr(test, path = "fake.rs")]', "#[path]"),
    ("src/lib.rs", "macro_rules! my_assert {", None),
    ("src/lib.rs", "// macro_rules! assert {", None),
    ("src/lib.c", "macro_rules! assert {", None),
    ("src/a.cc", "#define TEST(a, b) void f()", "#define TEST"),
    ("src/a.hpp", "#  undef \\\n  EXPECT_EQ", "#undef EXPECT_EQ"),
    ("src/a.cxx", "# define GTEST_SKIP() return", "#define GTEST_SKIP"),
    ("src/a.cc", "/* #define TEST */ int x;", None),
    ("src/a.cc", "#define TESTING 1", None),
    ("src/a.rs", "#define TEST 1", None),
  ],
)
def test_a_fix_that_rewires_tests_is_found(
  path: str, added: str, found: str | None
):
  assert core.rewires(path, added) == found


# What tells each module of GUARDS from the others, and its check's
# finding there.
GUARD_CASES = {
  "cargo": [("crates/spec/a.rs", "macro_rules! assert {")],
  "cpp": [("src/a.cc", "#define TEST 1")],
  "gotest": [("pkg/spec/a.go", "")],
}
HOST_FOUND = {
  "cargo": [[False, "macro_rules! assert"]],
  "cpp": [[False, "#define TEST"]],
  "gotest": [[False, None]],
}


@pytest.mark.parametrize("guard", GUARDS)
def test_a_copy_has_a_languages_checks_only_with_its_module(
  tmp_path: Path, guard: str
):
  # A copy with only the module, no runner of it, finds what the host
  # does; one without it does not, and a test's directory makes a Go or
  # Rust file a test's.
  cases = GUARD_CASES[guard]
  with_it = _checks(tmp_path / "with", files((), (), guards={guard}), cases)
  assert with_it == HOST_FOUND[guard]
  assert [[core.is_test_file(p), core.rewires(p, a)] for p, a in cases] == (
    HOST_FOUND[guard]
  )
  without = _checks(tmp_path / "without", files((), ()), cases)
  assert without == [[guard in ("gotest", "cargo"), None]]


@pytest.mark.parametrize(
  ("runners", "paths", "guards"),
  [
    ((), ["Gemfile", "lib/a.rb", "spec/a_spec.rb"], []),
    (("cargo",), [], ["cargo"]),
    (("go",), [], ["gotest"]),
    (("gtest",), [], ["cpp"]),
    (("ctest",), [], ["cpp"]),
    (("pytest",), ["Cargo.toml", "src/lib.rs", "a.py"], ["cargo"]),
    ((), ["include/a.hpp"], ["cpp"]),
    ((), ["src/a.cxx"], ["cpp"]),
    ((), ["go.mod", "x/a.go"], ["gotest"]),
    ((), ["a.rs", "b.c", "c.go"], ["gotest", "cargo", "cpp"]),
    ((), ["go.mod", "Cargo.toml", "rs", "a.rsx"], []),
  ],
)
def test_a_task_gets_the_guards_of_its_runners_and_its_languages(
  runners: tuple[str, ...], paths: list[str], guards: list[str]
):
  assert guards_for(runners, paths) == guards


def test_a_tasks_copy_names_the_guards_it_has():
  manifest = load(
    "[[run]]\nname = 'own'\nrun = ['ctest']\nparse = 'junit'\n"
    + "[[test]]\nsuite = 'p2p'\nrun = 'own'\nid = 'a'\n"
  )
  copied = files_for(manifest, ["src/a.cc", "CMakeLists.txt"])
  assert {"testrunner/cpp.py", "testrunner/junit.py"} <= set(copied)
  assert "testrunner/cargo.py" not in copied
  assert guards_of(copied["testrunner/registry.py"]) == ["cpp"]
  assert guards_of(registry(("go",), ("cpp",))) == ["gotest", "cpp"]
  assert guards_of(registry(())) == []
  with pytest.raises(ValueError, match="unknown guards: js"):
    _ = guards_of(
      registry(()).replace(
        b"GUARDS: tuple[str, ...] = ()", b"GUARDS: tuple[str, ...] = ('js',)"
      )
    )


@pytest.mark.parametrize(
  ("path", "vendored"),
  [
    ("vendor/x/a.go", True),
    ("vendor", True),
    ("sub/vendor/y/a.php", True),
    ("web/vendor/autoload.php", True),
    ("third/crate/src/lib.rs", True),
    ("other/vendor/a.go", False),
    ("third/crate", False),
    ("src/vendor.go", False),
  ],
)
def test_a_vendored_dependency_is_found(path: str, vendored: bool):
  # go.mod at the root, a composer.json in sub/, a PHPUnit test whose dir
  # is web/, and a crate cargo vendor wrote under third/.
  tests = [Test("p2p", "phpunit", "A::b", "web"), Test("p2p", "go", "x::T")]
  dirs = core.vendor_test_dirs(tests)
  assert dirs == ["web"]
  owners, crates = deps.vendor_dirs(
    ["go.mod", "sub/composer.json", "third/crate/.cargo-checksum.json"], dirs
  )
  assert (owners, crates) == ({"", "sub", "web"}, {"third/crate"})
  assert deps.vendored(path, owners, crates) is vendored


def test_a_copy_that_lacks_a_module_its_guards_name_fails(tmp_path: Path):
  # The checks never skip a module the copy's GUARDS names: one that is
  # missing stops them, so that no check goes unseen.
  copied = files((), (), guards={"cpp"})
  del copied["testrunner/cpp.py"]
  with pytest.raises(subprocess.CalledProcessError) as e:
    _ = _checks(tmp_path, copied, [("src/a.cc", "#define TEST 1")])
  stderr = cast("str", e.value.stderr)
  assert "ModuleNotFoundError: No module named 'testrunner.cpp'" in stderr


def test_the_vendor_dirs_are_those_of_phpunits_tests():
  # Those of the tests whose runner is phpunit, a preset group's included,
  # whose tests take its preset as their runner.
  manifest = load(
    "[[run]]\nname = 'php'\ndir = 'web'\npreset = 'phpunit'\n"
    + "[[run]]\nname = 'own'\ndir = 'own'\nrun = ['t']\nparse = 'junit'\n"
    + "[[test]]\nsuite = 'p2p'\nrun = 'php'\nid = 'A::b'\n"
    + "[[test]]\nsuite = 'p2p'\nrun = 'own'\nid = 'c'\n"
    + "[[test]]\nsuite = 'p2p'\nrunner = 'phpunit'\ndir = 'lib'\nid = 'C::d'\n"
    + "[[test]]\nsuite = 'p2p'\nrunner = 'go'\ndir = 'go'\nid = 'x::T'\n"
  )
  assert [t.runner for t in manifest.tests] == [
    "phpunit",
    None,
    "phpunit",
    "go",
  ]
  dirs = core.vendor_test_dirs(manifest.tests)
  assert dirs == [t.dir for t in manifest.tests if t.runner == "phpunit"]
  assert dirs == ["web", "lib"]

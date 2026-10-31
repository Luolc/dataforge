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


@pytest.mark.parametrize(
  ("added", "found"),
  [
    (
      "if (process.env.VITEST) {\n"
      + '  const { expect } = await import("vitest");\n'
      + "  expect.extend({ toBe() { return { pass: true } } });\n}",
      "expect.extend",
    ),
    (
      'import { expect as e } from "vitest";\ne.extend({ toBe: ok });',
      'e.extend, with "vitest" imported',
    ),
    (
      'const { expect: x } = require("@jest/globals");\nx.extend(m);',
      'x.extend, with "@jest/globals" imported',
    ),
    (
      'import * as m from "mocha";\nm.Runner.prototype.run = f;',
      'Runner.prototype, with "mocha" imported',
    ),
    ("expect.addSnapshotSerializer(s);", "expect.addSnapshotSerializer"),
    ("expect.toBe = () => {};", "expect.toBe ="),
    ("assert.strictEqual = () => {};", "assert.strictEqual ="),
    ("globalThis.expect = fake;", "globalThis.expect ="),
    ('window["expect"] ??= fake;', "window.expect ??="),
    ("global.it = function () {};", "global.it ="),
    ("(globalThis as any).expect = fake;", "globalThis.expect ="),
    ("(global as unknown as G).it = f;", "global.it ="),
    ("(<any>window).expect = fake;", "window.expect ="),
    ("(globalThis as Record<string, T>).expect = f;", "globalThis.expect ="),
    (
      'Object.defineProperty(globalThis as any, "expect", { value: f });',
      'Object.defineProperty(globalThis,"expect"',
    ),
    (
      'Object.defineProperty(<any>window, "it", { value: f });',
      'Object.defineProperty(window,"it"',
    ),
    (
      "Object.assign(globalThis as any, { expect: fake });",
      "Object.assign(globalThis,{expect",
    ),
    ("Reflect.set(global as any, 'test', f);", 'Reflect.set(global,"test"'),
    ("globalThis.expect.extend({ toBe: ok });", "globalThis.expect.extend"),
    ("globalThis.expect.toBe = f;", "globalThis.expect.toBe ="),
    ("const half = total / 2; globalThis.expect = f;", "globalThis.expect ="),
    ('import { use } from "chai";\nuse(plugin);', 'use(, with "chai" imported'),
    (
      'Object.defineProperty(globalThis, "describe", { value: d });',
      'Object.defineProperty(globalThis,"describe"',
    ),
    (
      "Object.assign(global, {\n  a,\n  test: t,\n});",
      "Object.assign(global,{a,test",
    ),
    ("jasmine.getEnv().addReporter(r);", "jasmine.getEnv"),
    ("chai.use(plugin);", "chai.use"),
    ("chai.Assertion.prototype.equal = f;", "chai.Assertion"),
    ('Assertion.overwriteMethod("equal", f);', "Assertion.overwriteMethod"),
    ("Object.setPrototypeOf(expect, fake);", "setPrototypeOf(expect"),
    (
      'globalThis[Symbol.for("$$jest-matchers-object")].x = 1;',
      '"$$jest-matchers-object"',
    ),
    # Lines added inside a template literal or a comment of the lines
    # before them, which they close and open again.
    ("`;\nglobalThis.expect = fake;\nconst s = `", "globalThis.expect ="),
    ("*/\nexpect.extend(m);\n/*", "expect.extend"),
    # A business function named expect, the frameworks' names in strings,
    # comments and regular expressions, a local named self, another
    # module, prototypes and setPrototypeOf on code of the project's own,
    # vitest's in-source tests and supertest's expect.
    ("export function expect(value) {\n  return new Check(value);\n}", None),
    ('const msg = "call expect.extend() in a setup file";', None),
    ("// expect.extend({}) adds matchers\n/* global.it = f */ a();", None),
    ("const re = /expect.extend|global.it = /g;", None),
    ('req.headers["expect"] = "100-continue";', None),
    ("const self = this;\nself.test = 1;", None),
    ('import { describe } from "./describe";\nfoo.extend(bar);', None),
    ('import assert from "node:assert";\nFoo.prototype.bar = 1;', None),
    ("Object.setPrototypeOf(this, HttpError.prototype);", None),
    (
      "if (import.meta.vitest) {\n"
      + "  const { it, expect } = import.meta.vitest;\n"
      + '  it("adds", () => expect(add(1, 1)).toBe(2));\n}',
      None,
    ),
    ("request(app).get('/').expect(200);", None),
    (
      'import { defineConfig } from "vitest/config";\n'
      + "export default defineConfig(base.extend({}));",
      None,
    ),
  ],
)
def test_a_js_fix_that_rewires_tests_is_found(added: str, found: str | None):
  assert core.rewires("src/a.ts", added) == found


@pytest.mark.parametrize(
  ("added", "found"),
  [
    ("import builtins\nbuiltins.print = lambda *a: None", "builtins.print ="),
    ('__builtins__["len"] = fake', '__builtins__["len"] ='),
    ('setattr(builtins, "isinstance", fake)', "setattr(builtins"),
    (
      "import builtins as b\nb.AssertionError = Exception",
      "b.AssertionError =",
    ),
    (
      "unittest.TestCase.assertEqual = lambda *a: None",
      "unittest.TestCase.assertEqual =",
    ),
    (
      "from unittest.case import (\n  Foo,\n  TestCase as Base,\n)\n"
      + 'setattr(Base, "assertIn", f)',
      "setattr(Base",
    ),
    (
      "del unittest.TestCase.assertRaises",
      "del unittest.TestCase.assertRaises",
    ),
    ("pytest.fail = lambda *a, **k: None", "pytest.fail ="),
    ('sys.modules["pytest"] = fake', 'sys.modules["pytest"'),
    (
      'patch.dict(sys.modules, {"unittest": fake}).start()',
      'sys.modules,{"unittest"',
    ),
    (
      "def pytest_collection_modifyitems(items):\n  items.clear()",
      ("def pytest_collection_modifyitems"),
    ),
    ('pytest_plugins = ["pkg.plugin"]', "pytest_plugins"),
    ('pytest.register_assert_rewrite("pkg")', "register_assert_rewrite"),
    ("from _pytest import assertion", "_pytest"),
    ("@pytest.hookimpl(tryfirst=True)\ndef f():\n  pass", "pytest.hookimpl"),
    ("config.pluginmanager.register(p)", "config.pluginmanager"),
    (
      'mock.patch("unittest.TestCase.assertEqual", f).start()',
      'patch("unittest.TestCase.assertEqual"',
    ),
    ('monkeypatch.setattr("pytest.approx", f)', 'setattr("pytest.approx"'),
    ('b = __import__("builtins")\nb.print = f', "b.print ="),
    ('__import__("builtins").print = f', '__import__("builtins").print ='),
    (
      'importlib.import_module("unittest").TestCase.run = f',
      'import_module("unittest").TestCase.run =',
    ),
    ("pytestconfig.pluginmanager.register(p)", "pytestconfig.pluginmanager"),
    ('vars(builtins)["print"] = f', 'vars(builtins)["print"] ='),
    ("builtins.__dict__.update(print=f)", "builtins.__dict__.update("),
    (
      'type.__setattr__(unittest.TestCase, "assertEqual", f)',
      "__setattr__(unittest",
    ),
    # Lines added inside a docstring of the lines before them, which they
    # close and open again.
    ('"""\nbuiltins.print = f\n"""', "builtins.print ="),
    # A business function named expect, the patterns in strings and
    # comments, the check for pytest a library makes, pytest's fixtures, a
    # method named as an assertion, a comparison and a patch of the
    # project's own code.
    ("def expect(value):\n  return value", None),
    ('msg = "builtins.print = x"\n# pytest_plugins = []', None),
    ('if "pytest" in sys.modules:\n  DEBUG = True', None),
    ("import pytest\n\n@pytest.fixture\ndef client():\n  return C()", None),
    ("class Check:\n  def assertValid(self):\n    self.ok = True", None),
    ("ok = pytest.approx(1.0) == y\nself.assertEqual(a, b)", None),
    ('from unittest import mock\nwith mock.patch("pkg.fn"):\n  pass', None),
    ("from unittest import mock\nmock.DEFAULT = 1", None),
    ("from unittest.mock import MagicMock\nMagicMock.return_value = 3", None),
    ("import unittest.mock as um\num.DEFAULT = 1", None),
    ("from unittest import mock as m\nm.DEFAULT = 1", None),
    ('self.pluginmanager = PluginManager("app")', None),
  ],
)
def test_a_python_fix_that_rewires_tests_is_found(
  added: str, found: str | None
):
  assert core.rewires("src/a.py", added) == found


@pytest.mark.parametrize(
  ("path", "found"),
  [
    ("lib/a.js", "expect.extend"),
    ("lib/a.mjs", "expect.extend"),
    ("lib/a.cjs", "expect.extend"),
    ("lib/a.jsx", "expect.extend"),
    ("lib/a.mts", "expect.extend"),
    ("lib/a.cts", "expect.extend"),
    ("lib/a.tsx", "expect.extend"),
    ("lib/a.pyi", None),
    ("lib/a.md", None),
    ("lib/a.go", None),
  ],
)
def test_the_js_and_python_checks_read_their_own_files(
  path: str, found: str | None
):
  assert core.rewires(path, "expect.extend(m)\nbuiltins.x = 1") == found


# What tells each module of GUARDS from the others, and its check's
# finding there.
GUARD_CASES = {
  "cargo": [("crates/spec/a.rs", "macro_rules! assert {")],
  "cpp": [("src/a.cc", "#define TEST 1")],
  "gotest": [("pkg/spec/a.go", "")],
  "jsguard": [("src/a.ts", "globalThis.expect = f")],
  "pyguard": [("src/a.py", "builtins.print = f")],
}
HOST_FOUND = {
  "cargo": [[False, "macro_rules! assert"]],
  "cpp": [[False, "#define TEST"]],
  "gotest": [[False, None]],
  "jsguard": [[False, "globalThis.expect ="]],
  "pyguard": [[False, "builtins.print ="]],
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
    (("pytest",), ["Cargo.toml", "src/lib.rs", "a.py"], ["cargo", "pyguard"]),
    (("jest", "pytest"), [], []),
    (("node",), ["a.py"], ["pyguard"]),
    ((), ["src/a.tsx", "b.cjs"], ["jsguard"]),
    ((), ["setup.py"], ["pyguard"]),
    (("go",), ["go.mod", "web/app.ts"], ["gotest", "jsguard"]),
    ((), ["a.pyi", "a.json", "a.vue"], []),
    ((), ["include/a.hpp"], ["cpp"]),
    ((), ["src/a.cxx"], ["cpp"]),
    ((), ["go.mod", "x/a.go"], ["gotest"]),
    ((), ["a.rs", "b.c", "c.go", "d.js", "e.py"], list(GUARDS)),
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
  assert guards_of(registry(("vitest", "pytest"))) == []
  assert guards_of(registry(("vitest",), ("pyguard", "jsguard"))) == [
    "jsguard",
    "pyguard",
  ]
  assert guards_of(registry(())) == []
  # A copy of the old layout, written before GUARDS, names none.
  old = registry(()).split(b"\nGUARDS")[0]
  with pytest.raises(ValueError, match="names no GUARDS: a copy of the old"):
    _ = guards_of(old)
  with pytest.raises(ValueError, match="unknown guards: maven"):
    _ = guards_of(
      registry(()).replace(
        b"GUARDS: tuple[str, ...] = ()",
        b"GUARDS: tuple[str, ...] = ('maven',)",
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


def test_the_package_managers_files_are_named():
  assert deps.PACKAGE_MANIFEST == "package.json"
  assert sorted(deps.SCRIPT_RUNNER_FILES) == [
    ".npmrc",
    ".pnp.cjs",
    ".pnp.loader.mjs",
    ".pnpmfile.cjs",
    ".yarnrc",
    ".yarnrc.yml",
    "pnpm-workspace.yaml",
  ]


@pytest.mark.parametrize(
  ("name", "config"),
  [
    # Each tool, and each extension of a *.config.* among them.
    ("tsdown.config.ts", True),
    ("tsup.config.cts", True),
    ("vite.config.mts", True),
    ("rollup.config.mjs", True),
    ("rolldown.config.ts", True),
    ("webpack.config.cjs", True),
    ("rspack.config.js", True),
    ("esbuild.config.mjs", True),
    ("babel.config.js", True),
    ("postcss.config.cjs", True),
    ("build.config.ts", True),
    ("babel.config.json", True),
    (".babelrc", True),
    (".babelrc.json", True),
    (".babelrc.js", True),
    (".babelrc.cjs", True),
    (".babelrc.mjs", True),
    (".babelrc.ts", True),
    (".babelrc.mts", True),
    (".babelrc.cts", True),
    (".postcssrc", True),
    (".postcssrc.json", True),
    (".postcssrc.yml", True),
    ("vite.config.helper.ts", False),
    # Vitest's own runner_files.
    ("vitest.config.ts", False),
    ("vite.config.json", False),
    (".babelrc.yml", False),
    ("tsconfig.json", False),
    ("config.ts", False),
    ("src/build.ts", False),
    # A name, never a path.
    ("src/vite.config.ts", False),
  ],
)
def test_build_config_matches_a_bundlers_config_by_its_whole_name(
  name: str, config: bool
):
  assert (deps.BUILD_CONFIG.fullmatch(name) is not None) is config


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

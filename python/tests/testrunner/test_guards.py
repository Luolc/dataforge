import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import cast

import pytest

from dataforge.testrunner import cargo, core, deps, phpguard
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
  [
    core.is_test_file(path),
    core.rewires(path, added),
    core.declares(added),
    core.functions(added),
    core.constants(added),
    core.defines(added),
  ]
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
    ("packages/editor/tests/view/styles.js", True),
    ("src/Component/Yaml/Tests/Fixtures/sample.yml", True),
    ("packages/editor/tests/helpers.py", False),
    ("crates/x/tests/it.rs", False),
    ("pkg/tests/helpers.go", False),
    ("sdk/src/main/java/org/x/tests/CaseService.java", False),
    ("packages/editor/latests/styles.js", False),
  ],
)
def test_a_test_file_is_told_by_its_path(path: str, test: bool):
  assert core.is_test_file(path) is test


@pytest.mark.parametrize(
  ("path", "test"),
  [
    ("tests", True),
    ("Test", True),
    ("lib/tests", True),
    ("packages/core/Tests", True),
    ("tests/x", False),
    ("sdk/src/main/java/org/x/tests", False),
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


# Each PHP text, and what declares gives for it, as PHP 8.3's own tokens
# have it, with the short open tag off or on, but for the line: a line
# ends only at `\n` here, as git counts lines, and at `\r` too in PHP.
PHP_CASES = [
  pytest.param(
    "<?php\nnamespace App\\Lib;\nfunction f() {}\nnamespace App\\Tests;\n",
    [(2, 2, "app\\lib"), (4, 4, "app\\tests")],
    id="two",
  ),
  pytest.param(
    "<?php declare(strict_types=1); namespace App\\Tests; function g() {}",
    [(1, 1, "app\\tests")],
    id="after-declare",
  ),
  pytest.param(
    "<?php\nnamespace App\\Lib;\n$a = '//'; namespace/**/App\\Tests;\n",
    [(2, 2, "app\\lib"), (3, 3, "app\\tests")],
    id="comment-in-a-string",
  ),
  pytest.param(
    "<?php\nNAMESPACE App\\Tests { }\n",
    [(2, 2, "app\\tests")],
    id="upper-case-braced",
  ),
  pytest.param(
    '<?php\n$s = "namespace App\\Tests;";\n# namespace App\\Tests;\n'
    + "// namespace App\\Tests;\n/* namespace App\\Tests; */\n",
    [],
    id="in-strings-and-comments",
  ),
  pytest.param(
    "<?php\nnamespace A;\n$s = <<<EOT\nnamespace App\\Tests;\nEOT;\n"
    + "namespace Ok;\n",
    [(2, 2, "a"), (6, 6, "ok")],
    id="in-a-heredoc",
  ),
  pytest.param(
    "<?php namespace Real; ?>\nnamespace NotCode;\n<?php namespace Also;\n",
    [(1, 1, "real"), (3, 3, "also")],
    id="html",
  ),
  pytest.param(
    "namespace NotCode;\n<?php function f() {}\n",
    [],
    id="html-before-the-tag",
  ),
  pytest.param(
    "<?php\n$x->namespace = 1; namespace\\f(); $y = $namespace;\n",
    [],
    id="not-declarations",
  ),
  pytest.param(
    '<?php\nnamespace App\\Lib;\n$s = "{$a[\'"\']}";\nnamespace App\\Tests;\n'
    + "$t = '';\n",
    [(2, 2, "app\\lib"), (4, 4, "app\\tests")],
    id="quote-in-an-interpolation",
  ),
  pytest.param(
    "<?php\nnamespace App\\Lib;\n$s = `${a['`']}`;\nnamespace App\\Tests;\n"
    + "$t = '';\n",
    [(2, 2, "app\\lib"), (4, 4, "app\\tests")],
    id="quote-in-a-dollar-brace",
  ),
  pytest.param(
    '<?php\nnamespace App\\Lib;\n$s = <<<EOT\n{$a["\nEOT"]}\nEOT;\n'
    + "namespace App\\Tests;\n",
    [(2, 2, "app\\lib"), (7, 7, "app\\tests")],
    id="label-in-a-heredocs-code",
  ),
  pytest.param(
    "<?php\nnamespace A;\n$s = <<<'EOT'\n{$a['\nEOT;\nnamespace Ok;\n",
    [(2, 2, "a"), (6, 6, "ok")],
    id="nowdoc",
  ),
  pytest.param(
    "<?php\n// a\rnamespace App\\Tests;\n",
    [(2, 2, "app\\tests")],
    id="carriage-return",
  ),
  pytest.param(
    "<?php\nnamespace A;\n$s = <<<EOT\r{$a['\r']}\rEOT;\r"
    + "namespace App\\Tests;\n// '\n",
    [(2, 2, "a"), (3, 3, "app\\tests")],
    id="heredoc-over-carriage-returns",
  ),
  pytest.param(
    "<?php\nnamespace A;\n#[A('\n')] function f() {}\nnamespace App\\Tests;\n"
    + "// '\n",
    [(2, 2, "a"), (5, 5, "app\\tests")],
    id="attribute",
  ),
  pytest.param(
    "<? namespace App\\Tests;\n",
    [(1, 1, "app\\tests")],
    id="short-open-tag",
  ),
  pytest.param(
    "<?php namespace A; ?>\n<?x 'a\n<?php namespace App\\Tests;\n// '\n",
    [(1, 1, "a"), (3, 3, "app\\tests")],
    id="short-open-tag-off",
  ),
  pytest.param(
    "<?php\nnamespace App\\Lib;\nx: namespace App\\Tests;\n",
    [(2, 2, "app\\lib"), (3, 3, "app\\tests")],
    id="after-a-label",
  ),
  pytest.param(
    "<?php namespace App\\Tests ?>\n",
    [(1, 1, "app\\tests")],
    id="ended-by-the-close-tag",
  ),
  pytest.param(
    "<?php\nnamespace\nApp\\Tests\n;\n",
    [(2, 4, "app\\tests")],
    id="over-lines",
  ),
  pytest.param(
    "<?php namespace ÄPP\\Tests;",
    [(1, 1, "Äpp\\tests")],
    id="non-ascii",
  ),
  pytest.param(
    "<?php\nnamespace A;\n$s = \"{$a['}\"']}\";\nnamespace App\\Tests;\n// '\n",
    [(2, 2, "a"), (4, 4, "app\\tests")],
    id="brace-and-quote-in-a-string-in-code",
  ),
  pytest.param(
    "<?php\nnamespace A;\n"
    + '$s = "{$a[(function () { return 1; })() . \'"\']}";\n'
    + "namespace App\\Tests;\n// '\n",
    [(2, 2, "a"), (4, 4, "app\\tests")],
    id="braces-in-a-strings-code",
  ),
  pytest.param(
    "<?php namespace A; // x ?> <?php namespace App\\Tests;\n",
    [(1, 1, "a"), (1, 1, "app\\tests")],
    id="close-tag-in-a-comment",
  ),
  pytest.param(
    "<?php\nnamespace A;\n$s = <<<EOT\r'\rEOT;\rnamespace App\\Tests;\n// '\n",
    [(2, 2, "a"), (3, 3, "app\\tests")],
    id="heredoc-opened-by-a-carriage-return",
  ),
  pytest.param(
    "<?php\nnamespace A;\n$s = '\\'';\nnamespace App\\Tests;\n// '\n",
    [(2, 2, "a"), (4, 4, "app\\tests")],
    id="escaped-quote",
  ),
  pytest.param(
    "<?php\nnamespace A;\n$s = <<<EOT\n  x\n  EOT;\nnamespace App\\Tests;\n",
    [(2, 2, "a"), (6, 6, "app\\tests")],
    id="indented-closing-label",
  ),
  pytest.param(
    "<?php\nnamespace A;\n$s = <<<EOT\nEOT;\nnamespace App\\Tests;\n"
    + "$t = <<<EOT\nx\nEOT;\n",
    [(2, 2, "a"), (5, 5, "app\\tests")],
    id="empty-heredoc",
  ),
  pytest.param(
    "<?php\nnamespace A;\n$s = <<<EOT\nEOTX '\nEOT;\nnamespace App\\Tests;\n"
    + "// '\n",
    [(2, 2, "a"), (6, 6, "app\\tests")],
    id="longer-label-in-a-heredoc",
  ),
]


@pytest.mark.parametrize(("text", "found"), PHP_CASES)
def test_the_namespaces_php_code_declares_are_found(
  text: str, found: list[tuple[int, int, str]]
):
  assert phpguard.declares(text) == found


# Each PHP text, and what functions gives for it: the functions PHP 8.3
# declares when it includes the text, with the short open tag on, and
# runs what it calls, as get_defined_functions()["user"] lists them, at
# the lines of `function` to the name.
PHP_FUNCTION_CASES = [
  pytest.param(
    "<?php\n"
    + "namespace App\\Tests;\n"
    + "function file_exists($f) { return false; }\n",
    [(3, 3, "app\\tests", "file_exists")],
    id="top",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "interface I { function count(); }\n"
    + "trait T { function strlen($s) {} }\n"
    + "enum E { case A; function trim() {} }\n"
    + "abstract class C {\n"
    + "  abstract function f();\n"
    + "  public static function g() {}\n"
    + "}\n",
    [],
    id="methods",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "class X { function m() { function strlen($s) { return 0; } } }\n"
    + "(new X)->m();\n",
    [(3, 3, "app", "strlen")],
    id="inside-a-method",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "if (true) {\n"
    + "  function strlen($s) { return 0; }\n"
    + "}\n",
    [(4, 4, "app", "strlen")],
    id="in-a-block",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "$f = function ($x) { return $x; };\n"
    + "$g = fn($x) => $x;\n"
    + "$h = static function &() { static $a; return $a; };\n",
    [],
    id="closures",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "$o = new class { function count() { return 0; } };\n"
    + "function trim($s) { return $s; }\n",
    [(4, 4, "app", "trim")],
    id="anonymous-class",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "class X {}\n"
    + "$o = null;\n"
    + "$c = X::class;\n"
    + "$d = $o?->class;\n"
    + "if ($c) { function strlen($s) { return 0; } }\n",
    [(7, 7, "app", "strlen")],
    id="class-constant",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App\\A {\n"
    + "  function count($a) { return 0; }\n"
    + "}\n"
    + "namespace {\n"
    + "  function my_own() {}\n"
    + "}\n",
    [(3, 3, "app\\a", "count"), (6, 6, "", "my_own")],
    id="braced",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + '$s = "function strlen() {}";\n'
    + "// function trim() {}\n"
    + "/* function count() {} */\n",
    [],
    id="in-strings-and-comments",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "function &count($a) { static $n = 0; return $n; }\n",
    [(3, 3, "app", "count")],
    id="by-reference",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "use function strlen;\n"
    + "use function Other\\trim as t;\n",
    [],
    id="use-function",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "function\n"
    + "strlen\n"
    + "($s) { return 0; }\n",
    [(3, 4, "app", "strlen")],
    id="split-over-lines",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "#[\\Deprecated]\n"
    + "function strlen($s) { return 0; }\n",
    [(4, 4, "app", "strlen")],
    id="attribute",
  ),
  pytest.param(
    "<?php\n"
    + "NAMESPACE App\\Tests;\n"
    + "FUNCTION File_Exists($f) { return false; }\n",
    [(3, 3, "app\\tests", "file_exists")],
    id="letter-case",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "class Enum {}\n"
    + "function wrap(Enum $e) {\n"
    + "  function strlen($s) { return 0; }\n"
    + "}\n"
    + "wrap(new Enum);\n"
    + "enum Suit: string { case A = 'a'; function count() { return 0; } }\n",
    [(4, 4, "app", "wrap"), (5, 5, "app", "strlen")],
    id="enum-as-a-type",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "$o = new class(function () { function strlen($s) { return 0; } }) {\n"
    + "  public function __construct(public $f) {}\n"
    + "};\n"
    + "($o->f)();\n",
    [(3, 3, "app", "strlen")],
    id="anonymous-class-with-a-closure",
  ),
  pytest.param(
    "<? namespace App; function strlen($s) { return 0; }\n",
    [(1, 1, "app", "strlen")],
    id="short-open-tag",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "class Enum {}\n"
    + "function make(): Enum {\n"
    + "  function strlen($s) { return 0; }\n"
    + "  return new Enum();\n"
    + "}\n"
    + "make();\n",
    [(4, 4, "app", "make"), (5, 5, "app", "strlen")],
    id="enum-as-a-return-type",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "class X {}\n"
    + "$m = [X::class => function () {\n"
    + "  function strlen($s) { return 0; }\n"
    + "}];\n"
    + "$m[X::class]();\n",
    [(5, 5, "app", "strlen")],
    id="a-class-constant-before-a-closure",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "if (!function_exists('App\\\\strlen')) {\n"
    + "  $x = 1;\n"
    + "}\n",
    [],
    id="a-call-of-function-exists",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "class X { const namespaces = 1; }\n"
    + "$n = X::namespaces;\n"
    + "function strlen($s) { return 0; }\n",
    [(5, 5, "app", "strlen")],
    id="a-name-that-starts-with-namespace",
  ),
  pytest.param(
    "<?php\n"
    + "namespace Acme\\Lib;\n"
    + "class_exists(class: 'X');\n"
    + "(function () { function file_exists($f) { return false; } })();\n",
    [(4, 4, "acme\\lib", "file_exists")],
    id="a-named-argument-then-a-closure",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "function call(...$a) {\n"
    + "  foreach ($a as $f) { if (is_callable($f)) { $f(); } }\n"
    + "}\n"
    + "call(class: 1, f: function () { function strlen($s) { return 0; } });\n",
    [(3, 3, "app", "call"), (6, 6, "app", "strlen")],
    id="a-named-argument-beside-a-closure",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "function make(): \\stdClass {\n"
    + "  function strlen($s) { return 0; }\n"
    + "  return new \\stdClass;\n"
    + "}\n"
    + "make();\n",
    [(3, 3, "app", "make"), (4, 4, "app", "strlen")],
    id="a-type-that-ends-in-a-keyword",
  ),
]


@pytest.mark.parametrize(("text", "found"), PHP_FUNCTION_CASES)
def test_the_functions_php_code_declares_are_found(
  text: str, found: list[tuple[int, int, str, str]]
):
  assert phpguard.functions(text) == found


def test_the_builtins_are_php_s_functions_with_no_namespace():
  assert {
    "file_exists",
    "strlen",
    "mb_strlen",
    "curl_init",
  } <= phpguard.BUILTINS
  assert all(re.fullmatch("[a-z0-9_]+", n) for n in phpguard.BUILTINS)


def test_core_gives_the_functions_and_the_builtins_of_the_guards():
  text = "<?php\nnamespace A;\nfunction strlen($s) {}\n"
  assert core.functions(text) == [(3, 3, "a", "strlen")]
  assert core.builtins() == phpguard.BUILTINS


# Each PHP text, and what constants gives for it: the constants PHP 8.3.6
# declares when it includes the text, with the short open tag on, as
# get_defined_constants(true)["user"] lists them, read when the cases were
# written, at the lines of `const` to the name.
PHP_CONSTANT_CASES = [
  pytest.param(
    "<?php\n" + "namespace App\\Tests;\n" + "const PHP_EOL = '';\n",
    [(3, 3, "app\\tests", "PHP_EOL")],
    id="top",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "const A = 1, B = [1, 2], C = (1 + 2) * 3, D = ['x' => [1, 2]];\n",
    [
      (3, 3, "app", "A"),
      (3, 3, "app", "B"),
      (3, 3, "app", "C"),
      (3, 3, "app", "D"),
    ],
    id="several",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "const\n"
    + "  E_ALL = 0,\n"
    + "  M_PI = 3;\n",
    [(3, 4, "app", "E_ALL"), (3, 5, "app", "M_PI")],
    id="split-over-lines",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "class K { const PHP_EOL = ''; public const int X = 1; }\n"
    + "interface I { const E_ALL = 0; }\n"
    + "trait T { const M_PI = 3; }\n"
    + "enum E { case A; const PHP_OS = 'x'; }\n",
    [],
    id="class-likes",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "use const PHP_EOL;\n"
    + "use const Other\\X as Y;\n"
    + "use Other\\{const Z, function f};\n"
    + "use Other\\{X, const W};\n",
    [],
    id="use-const",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "class K { const CONST = [1]; }\n"
    + "foreach (K::CONST as $v) {}\n"
    + "$o = new \\stdClass;\n"
    + "$o->const = 1;\n"
    + "const Y = K::CONST;\n",
    [(7, 7, "app", "Y")],
    id="members",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "function f($const) { return $const; }\n"
    + "f(const: 1);\n"
    + "const Z = 2;\n",
    [(5, 5, "app", "Z")],
    id="a-named-argument",
  ),
  pytest.param(
    "<?php\n" + "namespace App;\n" + "const A = 1;\n" + "echo A, PHP_EOL;\n",
    [(3, 3, "app", "A")],
    id="a-statement-after",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "foreach ([] as $const) { echo $const instanceof \\Countable; }\n"
    + "class ClassConst implements \\Countable {\n"
    + "  function count(): int { return 0; }\n"
    + "}\n"
    + "const MagicConst = 1;\n",
    [(7, 7, "app", "MagicConst")],
    id="in-names",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App\\A {\n"
    + "  const PHP_EOL = '';\n"
    + "}\n"
    + "namespace {\n"
    + "  const MY_OWN = 1;\n"
    + "}\n",
    [(3, 3, "app\\a", "PHP_EOL"), (6, 6, "", "MY_OWN")],
    id="braced",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "$s = 'const PHP_EOL = 1;';\n"
    + "// const E_ALL = 0;\n"
    + "/* const M_PI = 3; */\n"
    + "$h = <<<EOT\n"
    + "const X = 1;\n"
    + "EOT;\n",
    [],
    id="in-strings-and-comments",
  ),
  pytest.param(
    "<?php\n" + "NAMESPACE App\\Tests;\n" + "CONST Php_Eol = '';\n",
    [(3, 3, "app\\tests", "Php_Eol")],
    id="letter-case",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "const A = 1 ?>\n"
    + "text\n"
    + "<?php const B = 2;\n",
    [(3, 3, "app", "A"), (5, 5, "app", "B")],
    id="closing-tag",
  ),
  pytest.param(
    "<? namespace App; const PHP_EOL = '';\n",
    [(1, 1, "app", "PHP_EOL")],
    id="short-open-tag",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "$o = new class { const PHP_EOL = ''; };\n"
    + "const E_ALL = 0;\n",
    [(4, 4, "app", "E_ALL")],
    id="anonymous-class",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "class P { function __construct(public $a = 0, public $b = 0) {} }\n"
    + "const O = new P(1, 2), Q = 3;\n",
    [(4, 4, "app", "O"), (4, 4, "app", "Q")],
    id="new-in-an-initializer",
  ),
]


@pytest.mark.parametrize(("text", "found"), PHP_CONSTANT_CASES)
def test_the_constants_php_code_declares_are_found(
  text: str, found: list[tuple[int, int, str, str]]
):
  assert phpguard.constants(text) == found


def test_define_declares_no_constant_constants_finds():
  # Only running the code tells what define declares.
  text = "<?php\nnamespace App;\ndefine('App\\PHP_EOL', '');\n"
  assert phpguard.constants(text) == []


def test_the_builtin_constants_are_php_s_constants_with_no_namespace():
  assert {
    "PHP_EOL",
    "DIRECTORY_SEPARATOR",
    "E_ALL",
    "M_PI",
  } <= phpguard.BUILTIN_CONSTANTS
  assert not {"TRUE", "FALSE", "NULL"} & phpguard.BUILTIN_CONSTANTS
  assert all(
    re.fullmatch("[A-Za-z_][A-Za-z0-9_]*", n)
    for n in phpguard.BUILTIN_CONSTANTS
  )


def test_core_gives_the_constants_and_the_builtin_constants_of_the_guards():
  text = "<?php\nnamespace A;\nconst PHP_EOL = '';\n"
  assert core.constants(text) == [(3, 3, "a", "PHP_EOL")]
  assert core.builtin_constants() == phpguard.BUILTIN_CONSTANTS


# Each PHP text, and what defines gives for it: the constants PHP 8.3.6
# declares when it includes the text, with the short open tag on, and runs
# what it calls, as get_defined_constants(true)["user"] lists them, read
# when the cases were written, at the lines of `define` to the name.
PHP_DEFINE_CASES = [
  pytest.param(
    "<?php\n" + "define('APPPATH', '/app/');\n",
    [(2, 2, "", "APPPATH")],
    id="top",
  ),
  pytest.param(
    "<?php\n" + "define(\"CACHE_DIR\", 'x');\n",
    [(2, 2, "", "CACHE_DIR")],
    id="double-quoted",
  ),
  pytest.param(
    "<?php\n" + "namespace App;\n" + "define('ROOT', 1);\n",
    [(3, 3, "", "ROOT")],
    id="in-a-namespace",
  ),
  pytest.param(
    "<?php\n"
    + "define('Acme\\Lib\\LIMIT', 1);\n"
    + 'define("Acme\\\\Lib\\\\MAX", 2);\n'
    + "define('Acme\\\\Lib\\\\SIZE', 3);\n",
    [
      (2, 2, "acme\\lib", "LIMIT"),
      (3, 3, "acme\\lib", "MAX"),
      (4, 4, "acme\\lib", "SIZE"),
    ],
    id="a-namespaced-name",
  ),
  pytest.param(
    "<?php\n"
    + "namespace App;\n"
    + "\\define('X', 1);\n"
    + "DEFINE('Y', 2);\n",
    [(3, 3, "", "X"), (4, 4, "", "Y")],
    id="qualified-and-letter-case",
  ),
  pytest.param(
    "<?php\n" + "if (!defined('Z')) {\n" + "  define('Z', 1);\n" + "}\n",
    [(3, 3, "", "Z")],
    id="guarded",
  ),
  pytest.param(
    "<?php\n"
    + "class K {\n"
    + "  static function define($a, $b) {}\n"
    + "  function call() { self::define('V', 1); $this->define('W', 2); }\n"
    + "}\n"
    + "(new K)->call();\n"
    + "K::define('U', 3);\n",
    [],
    id="methods",
  ),
  pytest.param(
    "<?php\n"
    + "// define('C1', 1);\n"
    + "$s = \"define('C2', 1)\";\n"
    + "/* define('C3', 1); */\n",
    [],
    id="in-strings-and-comments",
  ),
  pytest.param(
    "<?php\n" + "define(\n" + "  'MULTI',\n" + "  1\n" + ");\n",
    [(2, 3, "", "MULTI")],
    id="split-over-lines",
  ),
  pytest.param(
    "<?php define('A1', 1) ?>\n" + "text\n" + "<?php define('A2', 2);\n",
    [(1, 1, "", "A1"), (3, 3, "", "A2")],
    id="closing-tag",
  ),
  pytest.param(
    "<? define('SHORT', 1);\n",
    [(1, 1, "", "SHORT")],
    id="short-open-tag",
  ),
  pytest.param(
    "<?php\n"
    + 'define("\\x41_HEX", 1);\n'
    + 'define("\\101_OCT", 2);\n'
    + 'define("\\u{42}_UNI", 3);\n',
    [(2, 2, "", "A_HEX"), (3, 3, "", "A_OCT"), (4, 4, "", "B_UNI")],
    id="escapes",
  ),
  pytest.param(
    "<?php\n"
    + "namespace Lib {\n"
    + "  function define($a, $b) {}\n"
    + "  function my_define($a, $b) {}\n"
    + "}\n"
    + "namespace {\n"
    + "  Lib\\define('N1', 1);\n"
    + "  \\Lib\\define('N2', 2);\n"
    + "  Lib\\my_define('N3', 3);\n"
    + "}\n",
    [],
    id="other-functions",
  ),
  pytest.param(
    "<?php\n"
    + "define( /* c */ 'X1', 1);\n"
    + "define('X3' /* c */, 1);\n"
    + "define(('X4'), 1);\n"
    + "define(\n"
    + "  // why\n"
    + "  ( 'X5' ),\n"
    + "  1\n"
    + ");\n",
    [(2, 2, "", "X1"), (3, 3, "", "X3"), (4, 4, "", "X4"), (5, 7, "", "X5")],
    id="comments-and-parentheses",
  ),
  pytest.param(
    "<?php\ndefine(constant_name: 'X2', value: 1);\n",
    [(2, 2, "", "X2")],
    id="the-argument-s-name",
  ),
  pytest.param(
    "<?php\n"
    + 'define("caf\\xc3\\xa9", 1);\n'
    + 'define("na\\303\\257ve", 2);\n',
    [(2, 2, "", "café"), (3, 3, "", "naïve")],
    id="utf-8-escapes",
  ),
]


@pytest.mark.parametrize(("text", "found"), PHP_DEFINE_CASES)
def test_the_constants_php_code_defines_are_found(
  text: str, found: list[tuple[int, int, str, str]]
):
  assert phpguard.defines(text) == found


@pytest.mark.parametrize(
  "call",
  [
    "define('A' . 'B', 1);",
    "define($name, 1);",
    'define("P_$x", 1);',
    "define(PREFIX . 'X', 1);",
    "define('IT\\'S', 1);",
    "define('\\Lead\\X', 1);",
    'define("A\\nB", 1);',
    'define("\\u{110000}X", 1);',
    "define(`hostname`, 1);",
    "f(define('X9'), 1);",
    "define(value: 1, constant_name: 'X10');",
  ],
  ids=[
    "a-concatenation",
    "a-variable",
    "an-interpolation",
    "a-constant",
    "no-name",
    "a-leading-backslash",
    "a-newline",
    "a-code-point-past-unicode",
    "a-command",
    "one-argument",
    "the-name-after-the-value",
  ],
)
def test_a_name_define_is_not_given_whole_is_not_read(call: str):
  # Only running the code tells a name it builds, one no code can name
  # shadows nothing, and a call of one argument declares nothing; a name
  # given after the value by its argument's name is left unread.
  assert phpguard.defines(f"<?php\n{call}\n") == []


def test_a_nul_in_the_code_is_no_string_of_a_define():
  # defines marks each string in the code it reads with NULs around its
  # number; a NUL of the code itself marks none.
  text = "<?php\ndefine(\x0099\x00, 1);\ndefine('OK', 1);\n"
  assert phpguard.defines(text) == [(3, 3, "", "OK")]


def test_core_gives_the_defines_of_the_guards():
  text = "<?php\ndefine('APPPATH', '/app/');\n"
  assert core.defines(text) == [(2, 2, "", "APPPATH")]


# What tells each module of GUARDS from the others, and its checks'
# findings there.
GUARD_CASES = {
  "cargo": [("crates/spec/a.rs", "macro_rules! assert {")],
  "cpp": [("src/a.cc", "#define TEST 1")],
  "gotest": [("pkg/spec/a.go", "")],
  "jsguard": [("src/a.ts", "globalThis.expect = f")],
  "pyguard": [("src/a.py", "builtins.print = f")],
  "phpguard": [
    (
      "src/a.php",
      "<?php\nnamespace A\\Tests; function strlen() {} const PHP_EOL = '';"
      + " define('APPPATH', '');",
    )
  ],
}
HOST_FOUND: dict[str, list[list[object]]] = {
  "cargo": [[False, "macro_rules! assert", [], [], [], []]],
  "cpp": [[False, "#define TEST", [], [], [], []]],
  "gotest": [[False, None, [], [], [], []]],
  "jsguard": [[False, "globalThis.expect =", [], [], [], []]],
  "pyguard": [[False, "builtins.print =", [], [], [], []]],
  "phpguard": [
    [
      False,
      None,
      [[2, 2, "a\\tests"]],
      [[2, 2, "a\\tests", "strlen"]],
      [[2, 2, "a\\tests", "PHP_EOL"]],
      [[2, 2, "", "APPPATH"]],
    ]
  ],
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
  host = [
    [
      core.is_test_file(p),
      core.rewires(p, a),
      [list(d) for d in core.declares(a)],
      [list(d) for d in core.functions(a)],
      [list(d) for d in core.constants(a)],
      [list(d) for d in core.defines(a)],
    ]
    for p, a in cases
  ]
  assert host == HOST_FOUND[guard]
  without = _checks(tmp_path / "without", files((), ()), cases)
  assert without == [[guard in ("gotest", "cargo"), None, [], [], [], []]]


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
    ((), ["composer.json", "src/A.php"], ["phpguard"]),
    (("phpunit",), ["src/A.php"], ["phpguard"]),
    ((), ["a.phtml", "b.inc"], []),
    ((), ["a.rs", "b.c", "c.go", "d.js", "e.py", "f.php"], list(GUARDS)),
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


@pytest.mark.parametrize(
  ("name", "config"),
  [
    # Each runner, and each extension its loader reads.
    ("jest.config.js", True),
    ("jest.config.cts", True),
    ("jest.config.json", True),
    ("vitest.config.mts", True),
    ("vitest.workspace.ts", True),
    ("vitest.workspace.json", True),
    ("vitest.projects.cjs", True),
    (".mocharc.cjs", True),
    (".mocharc.mjs", True),
    (".mocharc.yaml", True),
    (".mocharc.yml", True),
    (".mocharc.jsonc", True),
    (".mocharc.json", True),
    ("mocha.opts", True),
    ("ava.config.js", True),
    ("ava.config.cjs", True),
    ("ava.config.mjs", True),
    ("pytest.ini", True),
    (".pytest.ini", True),
    ("pytest.toml", True),
    (".pytest.toml", True),
    ("phpunit.xml", True),
    ("phpunit.dist.xml", True),
    ("phpunit.xml.dist", True),
    ("karma.conf.coffee", True),
    ("playwright.config.mjs", True),
    # An extension no loader reads, a name that only contains one, and a
    # file that is not a runner's own.
    ("jest.config.yaml", False),
    ("vitest.config.json", False),
    ("ava.config.json", False),
    (".mocharc", False),
    ("mocha.opts.bak", False),
    ("my.jest.config.js", False),
    ("jest.config.js.map", False),
    ("pytest.cfg", False),
    ("phpunit.xml.bak", False),
    ("vite.config.ts", False),
    ("pyproject.toml", False),
    ("conftest.py", False),
    # A name, never a path.
    ("src/jest.config.js", False),
    ("test/mocha.opts", False),
  ],
)
def test_runner_config_matches_a_runners_config_by_its_whole_name(
  name: str, config: bool
):
  assert (deps.RUNNER_CONFIG.fullmatch(name) is not None) is config


def test_the_runner_config_keys_are_named_by_file():
  assert {k: sorted(v) for k, v in deps.RUNNER_CONFIG_KEYS.items()} == {
    "package.json": ["ava", "jest", "mocha"],
    "pyproject.toml": ["tool.pytest", "tool.pytest.ini_options"],
    "setup.cfg": ["tool:pytest"],
    "tox.ini": ["pytest"],
  }
  # A file that has keys of a runner's is not matched whole by name.
  assert not any(
    deps.RUNNER_CONFIG.fullmatch(n) for n in deps.RUNNER_CONFIG_KEYS
  )


@pytest.mark.parametrize(
  ("path", "inside"),
  [
    (".cargo/config.toml", True),
    (".cargo/config", True),
    ("crates/a/.cargo/config.toml", True),
    # A link by the name, as git lists it.
    (".cargo", True),
    ("crates/a/.cargo", True),
    # A name that only contains one, or lacks its dot.
    ("cargo/config.toml", False),
    ("src/.cargo.rs", False),
    (".cargo-checksum.json", False),
    ("Cargo.toml", False),
  ],
)
def test_in_runner_config_dir_matches_a_part_of_a_path_whole(
  path: str, inside: bool
):
  assert deps.in_runner_config_dir(path) is inside


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

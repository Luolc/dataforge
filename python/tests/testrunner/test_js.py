import json
from pathlib import Path
import shutil
import subprocess
from typing import cast

import pytest

from dataforge.testrunner.adapter import Call, Context, find_bin, PREFIX
from dataforge.testrunner.ctrf import Case
from dataforge.testrunner.js import (
  covers,
  Jasmine,
  JASMINE_REPORT_ENV,
  JASMINE_REPORTER,
  Jest,
  jest_cases,
  Vitest,
)
from dataforge.testrunner.manifest import load
from dataforge.testrunner.manifest import Test as Listed
from dataforge.testrunner.run import run
from tests.testrunner.projects import commit, context, write

# The shape jest 30 and vitest 4 write with --json and --reporter=json.
JEST_REPORT: dict[str, object] = {
  "numTotalTests": 4,
  "testResults": [
    {
      "name": "{app}/src/attr.test.js",
      "status": "failed",
      "message": "attr fails",
      "assertionResults": [
        {
          "ancestorTitles": ["attr", "nested"],
          "title": "parses",
          "status": "passed",
          "duration": 3,
          "failureMessages": [],
        },
        {
          "ancestorTitles": ["attr"],
          "title": "fails",
          "status": "failed",
          "duration": 1.6,
          "failureMessages": ["Error: expected 1\n    at attr.test.js:3"],
        },
        {
          "ancestorTitles": ["attr"],
          "title": "waits",
          "status": "pending",
          "duration": None,
          "failureMessages": [],
        },
        {
          "ancestorTitles": [],
          "title": "top",
          "status": "todo",
          "failureMessages": [],
        },
      ],
    },
    {
      "name": "{app}/src/broken.test.js",
      "status": "failed",
      "message": "Test suite failed to run",
      "assertionResults": [],
    },
  ],
}


def jest_report(ctx: Context) -> dict[str, object]:
  text = json.dumps(JEST_REPORT).replace("{app}", str(ctx.app))
  return cast("dict[str, object]", json.loads(text))


def only(calls: list[Call]) -> list[str]:
  assert len(calls) == 1
  return calls[0].argv


def test_jest_cases_reads_a_jest_style_report(tmp_path: Path):
  ctx = context(tmp_path)
  file = "src/attr.test.js"
  assert jest_cases(ctx, jest_report(ctx)) == [
    Case(f"{file} > attr > nested > parses", "passed", 3, file=file),
    Case(
      f"{file} > attr > fails",
      "failed",
      2,
      message="Error: expected 1",
      trace="Error: expected 1\n    at attr.test.js:3",
      file=file,
    ),
    Case(f"{file} > attr > waits", "skipped", file=file),
    Case(f"{file} > top", "skipped", file=file),
    Case(
      "src/broken.test.js",
      "failed",
      error=True,
      message="Test suite failed to run",
      file="src/broken.test.js",
    ),
  ]


@pytest.mark.parametrize(
  ("outer", "inner", "covered"),
  [
    ("a.test.js > x", "a.test.js > x", True),
    ("a.test.js > x", "a.test.js > x > y", True),
    ("a.test.js", "a.test.js > x", True),
    ("a.test.js > x", "a.test.js > xy", False),
    ("a.test.js > x > y", "a.test.js > x", False),
  ],
)
def test_covers(outer: str, inner: str, covered: bool):
  assert covers(outer, inner) is covered


TESTS = [Listed("p2p", "jest", "web/src/a.test.js > a")]


def test_jest_copies_the_bases_jest_config(tmp_path: Path):
  ctx = context(tmp_path)
  base = commit(
    ctx.app,
    {
      "web/package.json": '{"jest": {"testEnvironment": "jsdom"}}',
      "web/jest.config.js": "module.exports = {};\n",
      "web/src/a.test.js": "",
    },
  )
  ctx = context(tmp_path, "web", base)
  _ = (ctx.dir / "jest.config.js").write_text("module.exports = {bail: 1};\n")
  runner = Jest()
  copy = ctx.dir / f"{PREFIX}jest.config.js"
  assert runner.prepare(ctx, TESTS) == [copy]
  assert copy.read_text() == "module.exports = {};\n"
  argv = only(runner.commands(ctx, TESTS))
  assert argv[0] == "jest" and f"--config={copy}" in argv
  assert argv[-2:] == ["--runTestsByPath", "src/a.test.js"]


def test_jest_takes_package_jsons_jest_from_the_base(tmp_path: Path):
  ctx = context(tmp_path)
  base = commit(
    ctx.app,
    {
      "web/package.json": '{"jest": {"testEnvironment": "node"}}',
      "web/src/a.test.js": "",
    },
  )
  ctx = context(tmp_path, "web/src", base)
  # A fix's own configuration file is not read.
  write(ctx.app, {"web/jest.config.js": "module.exports = {bail: 1};\n"})
  runner = Jest()
  assert runner.prepare(ctx, TESTS) == []
  argv = only(runner.commands(ctx, TESTS))
  config = next(a for a in argv if a.startswith("--config="))
  assert json.loads(config.removeprefix("--config=")) == {
    "testEnvironment": "node",
    "rootDir": str(ctx.app / "web"),
  }


def test_jest_without_a_configuration_has_its_package_as_the_root(
  tmp_path: Path,
):
  ctx = context(tmp_path)
  base = commit(ctx.app, {"package.json": "{}", "web/src/a.test.js": ""})
  ctx = context(tmp_path, "web", base)
  write(ctx.dir, {"package.json": '{"jest": {"bail": 1}}'})
  runner = Jest()
  _ = runner.prepare(ctx, TESTS)
  argv = only(runner.commands(ctx, TESTS))
  assert f"--config={json.dumps({'rootDir': str(ctx.app)})}" in argv


def test_vitest_copies_the_nearest_config_or_writes_an_empty_one(
  tmp_path: Path,
):
  ctx = context(tmp_path)
  base = commit(
    ctx.app,
    {"vite.config.ts": "export default {};\n", "web/src/a.test.js": ""},
  )
  tests = [Listed("p2p", "vitest", "web/src/a.test.js > a")]
  ctx = context(tmp_path, "web", base)
  write(ctx.dir, {"vitest.config.ts": "export default {test: {}};\n"})
  runner = Vitest()
  copy = ctx.app / f"{PREFIX}vite.config.ts"
  assert runner.prepare(ctx, tests) == [copy]
  argv = only(runner.commands(ctx, tests))
  assert argv[:3] == ["vitest", "run", f"--config={copy}"]
  assert argv[-1] == "src/a.test.js"
  report = next(a for a in argv if a.startswith("--outputFile.json="))
  assert report == f"--outputFile.json={ctx.work / 'vitest.json'}"
  empty = context(tmp_path / "none")
  assert Vitest().prepare(empty, tests) == [
    empty.app / f"{PREFIX}vitest.config.mjs"
  ]


def test_find_bin_takes_a_hoisted_tool(tmp_path: Path):
  ctx = context(tmp_path, "packages/web")
  write(ctx.app, {"node_modules/.bin/jest": "", "packages/web/x": ""})
  assert find_bin(ctx, "jest") == str(ctx.app / "node_modules/.bin/jest")
  assert find_bin(ctx, "vitest") == "vitest"


def test_jasmine_runs_each_file_alone_and_reads_its_reports(tmp_path: Path):
  ctx = context(tmp_path)
  write(ctx.app, {"spec/a_spec.js": "", "spec/b_spec.js": ""})
  tests = [
    Listed("p2p", "jasmine", "spec/a_spec.js > a"),
    Listed("p2p", "jasmine", "spec/b_spec.js > b"),
    Listed("p2p", "jasmine", "spec/a_spec.js > c"),
  ]
  runner = Jasmine()
  calls = runner.commands(ctx, tests)
  reports = [ctx.work / "jasmine-0.json", ctx.work / "jasmine-1.json"]
  assert calls == [
    Call(
      ["jasmine", f"--reporter={JASMINE_REPORTER}", "spec/a_spec.js"],
      {JASMINE_REPORT_ENV: str(reports[0])},
    ),
    Call(
      ["jasmine", f"--reporter={JASMINE_REPORTER}", "spec/b_spec.js"],
      {JASMINE_REPORT_ENV: str(reports[1])},
    ),
  ]
  assert runner.cases(ctx) is None
  spec = {"titles": ["a", "x"], "status": "excluded", "error": False}
  report = {"specs": [spec], "errors": ["afterAll threw"]}
  _ = reports[0].write_text(json.dumps(report))
  assert runner.cases(ctx) == [
    Case(
      "spec/a_spec.js",
      "failed",
      error=True,
      message="afterAll threw",
      file="spec/a_spec.js",
    ),
    Case("spec/a_spec.js > a > x", "skipped", file="spec/a_spec.js"),
  ]


NODE = shutil.which("node")
DRIVE = """
const Reporter = require(process.argv[1]);
const r = new Reporter();
const withParents = process.argv[2] === "yes";
const p = (id) => (withParents ? { parentSuiteId: id } : {});
r.suiteStarted({ id: "s1", description: "outer", ...p(null) });
r.suiteStarted({ id: "s2", description: "inner", ...p("s1") });
r.specDone({ description: "deep", status: "passed", duration: 2, ...p("s2") });
r.suiteDone({ id: "s2", failedExpectations: [] });
r.specDone({
  description: "fails",
  status: "failed",
  failedExpectations: [{ message: "Expected 1", stack: "at x" }],
  ...p("s1"),
});
r.suiteDone({ id: "s1", failedExpectations: [{ message: "afterAll" }] });
r.jasmineDone({ failedExpectations: [{ message: "load" }] });
"""


@pytest.mark.parametrize("parents", ["yes", "no"])
def test_the_jasmine_reporter_names_specs_by_their_suites(
  tmp_path: Path, parents: str
):
  # Jasmine 4 and later give each result its parentSuiteId; 3 does not.
  assert NODE is not None, "the tests need node on the PATH"
  report = tmp_path / "report.json"
  _ = subprocess.run(
    [NODE, "-e", DRIVE, str(JASMINE_REPORTER), parents],
    check=True,
    env={"PATH": "/usr/bin:/bin", JASMINE_REPORT_ENV: str(report)},
  )
  assert json.loads(report.read_text()) == {
    "specs": [
      {
        "titles": ["outer", "inner", "deep"],
        "status": "passed",
        "error": False,
        "duration": 2,
        "message": None,
        "trace": None,
      },
      {
        "titles": ["outer", "fails"],
        "status": "failed",
        "error": False,
        "duration": 0,
        "message": "Expected 1",
        "trace": "at x",
      },
      {
        "titles": ["outer"],
        "status": "failed",
        "error": True,
        "duration": 0,
        "message": "afterAll",
        "trace": "afterAll",
      },
    ],
    "errors": ["load"],
  }


NODE_TESTS = {
  "test/a.test.js": """const test = require("node:test");
const assert = require("node:assert");
test.describe("attr", () => {
  test.describe("nested", () => {
    test.it("parses", () => {});
  });
  test.it("fails", () => {
    assert.strictEqual(1, 2);
  });
  test.it.skip("waits", () => {});
});
test("with sub", async (t) => {
  await t.test("one", () => {});
});
test("unlisted", () => {});
""",
  "test/broken.test.js": "syntax error (\n",
}
NODE_MANIFEST = """
[[test]]
suite = "p2p"
runner = "node"
id = "test/a.test.js > attr"

[[test]]
suite = "p2p"
runner = "node"
id = "test/a.test.js > with sub > one"

[[test]]
suite = "p2p"
runner = "node"
id = "test/broken.test.js > loads"
"""


def test_node_runs_each_file_with_its_junit_reporter(tmp_path: Path):
  assert NODE is not None, "the tests need node on the PATH"
  app, tests = tmp_path / "app", tmp_path / "tests"
  write(app, NODE_TESTS)
  tests.mkdir()
  found = run(load(NODE_MANIFEST), tests=tests, app=app)
  assert [(t["name"], t["status"], t.get("rawStatus")) for t in found] == [
    ("test/a.test.js > attr > nested > parses", "passed", None),
    ("test/a.test.js > attr > fails", "failed", None),
    ("test/a.test.js > attr > waits", "skipped", None),
    ("test/a.test.js > with sub > one", "passed", None),
    ("test/broken.test.js", "failed", "error"),
  ]
  assert "strictly equal" in str(found[1]["message"])

import json
from pathlib import Path
from typing import cast

import pytest

from dataforge.testrunner import ctrf
from dataforge.testrunner import run as runs
from dataforge.testrunner.manifest import load
from dataforge.testrunner.run import Results, run
from tests.testrunner.projects import write

# A run that writes the JUnit XML in report.xml, beside it in tests/, to
# TESTRUNNER_OUT, and what it was started with to the project.
RUN = """cp "$(dirname "$0")/report.xml" "$TESTRUNNER_OUT/r.xml"
pwd > ran.txt
echo "$TESTRUNNER_DIR" >> ran.txt
"""
JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites><testsuite name="s">
<testcase classname="spec/a_spec.rb" name="adds" time="0.004"/>
<testcase classname="spec/a_spec.rb" name="fails">
<failure message="expected 1">at a_spec.rb:3</failure></testcase>
<testcase classname="spec/a_spec.rb" name="errs">
<error message="boom"/></testcase>
<testcase classname="spec/a_spec.rb" name="skips"><skipped/></testcase>
<testcase classname="spec/a_spec.rb" name="twice"/>
<testcase classname="spec/a_spec.rb" name="twice"><failure/></testcase>
<testcase classname="spec/a_spec.rb" name="canary"><failure/></testcase>
<testcase classname="spec/b_spec.rb" name="unlisted"/>
<testcase name="bare"/>
</testsuite></testsuites>
"""
MANIFEST = """
[[run]]
name = "rspec"
dir = "sub"
run = ["sh", "run.sh"]
parse = "junit"

[[test]]
suite = "p2p"
run = "rspec"
id = "spec/a_spec.rb::adds"

[[test]]
suite = "f2p"
run = "rspec"
id = "spec/a_spec.rb::fails"
kind = "vulnerability"

[[test]]
suite = "p2p"
run = "rspec"
id = "spec/a_spec.rb::errs"

[[test]]
suite = "p2p"
run = "rspec"
id = "spec/a_spec.rb::skips"

[[test]]
suite = "p2p"
run = "rspec"
id = "spec/a_spec.rb::twice"

[[test]]
suite = "p2p"
run = "rspec"
id = "spec/a_spec.rb::gone"

[[test]]
suite = "p2p"
run = "rspec"
id = "spec/a_spec.rb::canary"
expect = "failed"
"""


def project(
  tmp_path: Path, manifest: str = MANIFEST, files: dict[str, str] | None = None
) -> tuple[Path, Path]:
  app, tests = tmp_path / "app", tmp_path / "tests"
  write(app, {"sub/keep.txt": ""})
  write(
    tests,
    {"manifest.toml": manifest, "run.sh": RUN, "report.xml": JUNIT}
    | (files or {}),
  )
  return app, tests


def outcomes(found: Results) -> list[tuple[object, ...]]:
  return [
    (t["suite"], t["name"], t["status"], t.get("rawStatus"))
    for t in found.tests
  ]


def test_a_group_runs_its_own_command_in_its_dir(tmp_path: Path):
  app, tests = project(tmp_path)
  found = run(load(MANIFEST), tests=tests, app=app)
  sub = str((app / "sub").resolve())
  assert (app / "sub" / "ran.txt").read_text() == f"{sub}\n{sub}\n"
  assert outcomes(found) == [
    (["p2p"], "spec/a_spec.rb::adds", "passed", None),
    (["f2p"], "spec/a_spec.rb::fails", "failed", None),
    (["p2p"], "spec/a_spec.rb::errs", "failed", "error"),
    (["p2p"], "spec/a_spec.rb::skips", "skipped", None),
    (["p2p"], "spec/a_spec.rb::twice", "passed", None),
    (["p2p"], "spec/a_spec.rb::twice", "failed", None),
    (["p2p"], "spec/a_spec.rb::gone", "failed", "error"),
    (["p2p"], "spec/a_spec.rb::canary", "failed", None),
  ]
  assert found.tests[1]["labels"] == {
    "id": "f2p::spec/a_spec.rb::fails",
    "run": "rspec",
    "kind": "vulnerability",
  }
  assert found.unlisted == [
    {"run": "rspec", "total": 2, "names": ["bare", "spec/b_spec.rb::unlisted"]}
  ]


def test_a_listed_name_the_groups_report_lacks_is_an_error(tmp_path: Path):
  app, tests = project(tmp_path)
  found = run(load(MANIFEST), tests=tests, app=app)
  gone = found.tests[6]
  assert gone["message"] == f"{ctrf.MISSING} (the run exited 0)"


def test_a_skipped_test_and_a_name_with_a_failed_case_do_not_pass(
  tmp_path: Path,
):
  app, tests = project(tmp_path)
  found = run(load(MANIFEST), tests=tests, app=app)
  report = ctrf.report(found.tests, 0, 0, unlisted=found.unlisted)
  # adds passes; errs, skips, one case of twice and gone do not.
  assert ctrf.suite_counts(report) == {"p2p": (2, 6), "f2p": (0, 1)}


def test_a_canary_counts_in_no_suite_and_a_failed_one_is_not_broken(
  tmp_path: Path,
):
  app, tests = project(tmp_path)
  found = run(load(MANIFEST), tests=tests, app=app)
  canary = found.tests[-1]
  assert cast("dict[str, str]", canary["labels"])["expect"] == "failed"
  report = ctrf.report(found.tests, 0, 0)
  assert ctrf.suite_counts(report)["p2p"][1] == 6
  assert "extra" not in cast("dict[str, object]", report["results"])


ALL_PASSED = """<testsuite>
<testcase classname="spec/a_spec.rb" name="adds"/>
<testcase classname="spec/a_spec.rb" name="canary"/>
</testsuite>
"""


def test_a_report_made_all_passed_breaks_the_canary(tmp_path: Path):
  app, tests = project(tmp_path, files={"report.xml": ALL_PASSED})
  found = run(load(MANIFEST), tests=tests, app=app)
  report = ctrf.report(found.tests, 0, 0, unlisted=found.unlisted)
  extra = cast("dict[str, object]", report["results"])["extra"]
  assert extra == {"canary_broken": ["p2p::spec/a_spec.rb::canary"]}


def test_a_canary_that_errs_or_is_missing_is_broken():
  def canary(status: str, raw: str | None = None) -> ctrf.TestObject:
    found: ctrf.TestObject = {"name": "c", "status": status, "suite": ["p2p"]}
    if raw:
      found["rawStatus"] = raw
    found["labels"] = {"id": "p2p::c", "expect": "failed"}
    return found

  assert ctrf.canary_broken([canary("failed")]) == []
  assert ctrf.canary_broken([canary("failed", "error")]) == ["p2p::c"]
  assert ctrf.canary_broken([canary("skipped")]) == ["p2p::c"]
  assert ctrf.canary_broken([canary("failed"), canary("passed")]) == ["p2p::c"]


def test_a_report_that_cannot_be_read_errs_each_test_of_its_group(
  tmp_path: Path,
):
  # One malformed test makes the whole report unreadable, its good ones too.
  bad = {
    "results": {
      "tests": [
        {"name": "spec/a_spec.rb::adds", "status": "passed"},
        {"name": "x"},
      ]
    }
  }
  run_sh = 'cp "$(dirname "$0")/bad.json" "$TESTRUNNER_OUT/r.json"\n'
  manifest = MANIFEST.replace('parse = "junit"', 'parse = "ctrf"')
  app, tests = project(
    tmp_path, manifest, {"run.sh": run_sh, "bad.json": json.dumps(bad)}
  )
  found = run(load(manifest), tests=tests, app=app)
  assert {(t["status"], t.get("rawStatus")) for t in found.tests} == {
    ("failed", "error")
  }
  assert "x: status None is not CTRF's" in str(found.tests[0]["message"])


def test_a_group_with_no_report_errs_each_test(tmp_path: Path):
  app, tests = project(tmp_path, files={"run.sh": "exit 3\n"})
  found = run(load(MANIFEST), tests=tests, app=app)
  assert found.tests[0]["message"] == (
    f"{ctrf.MISSING} (the run exited 3, with no report)"
  )


PARSE_MANIFEST = """
[[run]]
name = "own"
run = ["sh", "run.sh"]
parse = ["sh", "parse.sh"]
outputs = ["build/results/**"]

[[test]]
suite = "p2p"
run = "own"
id = "t1"
"""
# A framework that writes only inside the project, and a parse of its own.
OWN_RUN = """test -e build/results/old.txt && exit 9
mkdir -p build/results && echo "t1 ok" > build/results/out.txt
cp build/results/out.txt "$TESTRUNNER_OUT/"
"""
OWN_PARSE = """test -n "$TESTRUNNER_CTRF" || exit 1
read name word < "$TESTRUNNER_OUT/out.txt"
status=failed; [ "$word" = ok ] && status=passed
printf '{"results": {"tests": [{"name": "%s", "status": "%s"}]}}' \\
  "$name" "$status" > "$TESTRUNNER_CTRF"
"""


def test_outputs_go_before_the_run_and_a_parse_argv_writes_ctrf(
  tmp_path: Path,
):
  app, tests = project(
    tmp_path,
    PARSE_MANIFEST,
    {"run.sh": OWN_RUN, "parse.sh": OWN_PARSE},
  )
  write(app, {"build/results/old.txt": "planted"})
  found = run(load(PARSE_MANIFEST), tests=tests, app=app)
  assert outcomes(found) == [(["p2p"], "t1", "passed", None)]
  assert not (app / "build/results/old.txt").exists()


def test_a_preset_group_runs_as_its_runner(tmp_path: Path):
  app, tests = tmp_path / "app", tmp_path / "tests"
  write(app, {"t.py": "def test_a():\n  pass\n"})
  manifest = """
[[run]]
name = "unit"
preset = "pytest"

[[test]]
suite = "p2p"
run = "unit"
id = "t.py::test_a"
"""
  write(tests, {"manifest.toml": manifest})
  found = run(load(manifest), tests=tests, app=app)
  assert outcomes(found) == [(["p2p"], "t.py::test_a", "passed", None)]
  assert found.tests[0]["labels"] == {
    "id": "p2p::t.py::test_a",
    "runner": "pytest",
    "run": "unit",
  }


def test_a_group_keeps_at_most_the_limit_of_unlisted_names(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setattr(runs, "UNLISTED_LIMIT", 1)
  app, tests = project(tmp_path)
  found = run(load(MANIFEST), tests=tests, app=app)
  assert found.unlisted == [{"run": "rspec", "total": 2, "names": ["bare"]}]

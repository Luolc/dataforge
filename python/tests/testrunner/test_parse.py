import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import cast

import pytest

from dataforge.testrunner import files, junit, parse
from dataforge.testrunner.ctrf import Case
from dataforge.testrunner.parse import read, ReportError
from tests.testrunner.projects import write

JUNIT = """<testsuites><testsuite name="s">
<testcase classname="Math" name="adds" time="0.004" file="t/math.c"/>
<testcase name="bare"><failure message="no">trace</failure></testcase>
<testcase classname="Math" name="off" status="notrun"/>
</testsuite></testsuites>
"""


def test_junit_names_a_case_by_its_class_and_name(tmp_path: Path):
  write(tmp_path, {"a/r.xml": JUNIT, "a/b/r.json": "{}", "a/b/s.xml": JUNIT})
  cases = read("junit", tmp_path / "a")
  assert cases is not None
  assert cases[:3] == [
    Case("Math::adds", "passed", 4, file="t/math.c"),
    Case("bare", "failed", message="no", trace="trace"),
    Case("Math::off", "skipped"),
  ]
  assert len(cases) == 6
  assert read("junit", tmp_path / "none") is None


def test_ctrf_reads_name_and_status_and_rawstatus_only_makes_it_worse(
  tmp_path: Path,
):
  tests = [
    {"name": "a", "status": "passed", "duration": 2.4, "suite": ["f2p"]},
    {"name": "b", "status": "pending"},
    {"name": "c", "status": "other", "message": 3},
    {"name": "d", "status": "passed", "rawStatus": "error"},
    {"name": "e", "status": "failed", "rawStatus": "AssertionError"},
    {"name": "f", "status": "skipped"},
  ]
  write(tmp_path, {"r.json": json.dumps({"results": {"tests": tests}})})
  assert read("ctrf", tmp_path) == [
    Case("a", "passed", 2),
    Case("b", "skipped"),
    Case("c", "failed"),
    Case("d", "failed", error=True),
    Case("e", "failed"),
    Case("f", "skipped"),
  ]


@pytest.mark.parametrize(
  ("text", "problem"),
  [
    ("not json", "not UTF-8 JSON"),
    ('{"results": {}}', "no results.tests array"),
    ('{"results": {"tests": [{"name": "a\\nb", "status": "passed"}]}}', "name"),
    ('{"results": {"tests": [{"name": "a", "status": "ok"}]}}', "'ok'"),
  ],
)
def test_a_ctrf_report_not_of_its_shape_cannot_be_read(
  tmp_path: Path, text: str, problem: str
):
  write(tmp_path, {"r.json": text})
  with pytest.raises(ReportError, match=problem):
    _ = read("ctrf", tmp_path)


def test_a_file_over_the_limit_cannot_be_read(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  text = '{"results": {"tests": []}}'
  monkeypatch.setattr(parse, "LIMIT", len(text))
  write(tmp_path, {"a/r.json": text, "b/r.json": text + " "})
  assert read("ctrf", tmp_path / "a") == []
  with pytest.raises(ReportError, match=r"r\.json is over 26 bytes"):
    _ = read("ctrf", tmp_path / "b")


def test_a_junit_name_must_be_one_line(tmp_path: Path):
  write(
    tmp_path, {"r.xml": '<testsuite><testcase name="a&#10;b"/></testsuite>'}
  )
  with pytest.raises(ReportError, match="no one-line name"):
    _ = read("junit", tmp_path)
  write(tmp_path, {"r.xml": '<testsuite><testcase classname="c"/></testsuite>'})
  with pytest.raises(ReportError, match="no one-line name"):
    _ = read("junit", tmp_path)


def test_a_report_not_in_utf_8_cannot_be_read(tmp_path: Path):
  cp1252 = '<?xml version="1.0" encoding="windows-1252"?>\n' + JUNIT
  write(tmp_path, {"r.xml": cp1252})
  assert junit.root(tmp_path / "r.xml") is None
  with pytest.raises(ReportError, match="not UTF-8 JUnit XML"):
    _ = read("junit", tmp_path)
  _ = (tmp_path / "r.xml").write_bytes(
    JUNIT.replace("bare", "b\xe9").encode("latin-1")
  )
  assert junit.root(tmp_path / "r.xml") is None


def test_the_parse_command_writes_ctrf_on_python_3_12(tmp_path: Path):
  python = shutil.which("python3.12")
  assert python is not None, "the tests need python3.12 on the PATH"
  tests = tmp_path / "tests"
  write(
    tests, {name: data.decode() for name, data in files((), ("junit",)).items()}
  )
  write(tmp_path, {"r.xml": JUNIT})
  out = tmp_path / "out.json"
  _ = subprocess.run(
    [python, "-S", "-m", "testrunner", "parse", "junit", "../r.xml", str(out)],
    cwd=tests,
    check=True,
    env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
  )
  report = cast(
    "dict[str, dict[str, list[dict[str, str]]]]", json.loads(out.read_text())
  )
  assert [(t["name"], t["status"]) for t in report["results"]["tests"]] == [
    ("Math::adds", "passed"),
    ("bare", "failed"),
    ("Math::off", "skipped"),
  ]
  assert "suite" not in report["results"]["tests"][0]
  done = subprocess.run(
    [python, "-S", "-m", "testrunner", "parse", "ctrf", "../none", str(out)],
    cwd=tests,
    capture_output=True,
    text=True,
    check=False,
  )
  assert (done.returncode, done.stderr) == (1, "testrunner parse: no report\n")

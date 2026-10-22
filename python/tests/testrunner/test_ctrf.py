import json
from pathlib import Path
from typing import cast

import pytest

from dataforge.testrunner import ctrf
from dataforge.testrunner.ctrf import Case
from dataforge.testrunner.manifest import Test as Listed

TEST = Listed(
  "f2p", "pytest", "tests/test_a.py::test_x", kind="alignment", principle="p1"
)


def test_entry_writes_a_case_as_a_ctrf_test():
  case = Case(
    "tests/test_a.py::test_x[1]",
    "failed",
    12,
    error=True,
    message="boom",
    trace="Traceback",
    file="tests/test_a.py",
  )
  assert ctrf.entry(case, TEST.suite, ctrf.labels(TEST)) == {
    "name": "tests/test_a.py::test_x[1]",
    "status": "failed",
    "duration": 12,
    "suite": ["f2p"],
    "rawStatus": "error",
    "message": "boom",
    "trace": "Traceback",
    "filePath": "tests/test_a.py",
    "labels": {
      "id": "f2p::tests/test_a.py::test_x",
      "runner": "pytest",
      "kind": "alignment",
      "principle": "p1",
    },
  }


def test_a_passed_case_has_no_raw_status_and_a_test_no_absent_labels():
  plain = Listed("p2p", "pytest", "t.py::test_y")
  found = ctrf.entry(Case("t.py::test_y", "passed"), "p2p", ctrf.labels(plain))
  assert "rawStatus" not in found and "message" not in found
  assert found["labels"] == {"id": "p2p::t.py::test_y", "runner": "pytest"}


def test_a_missing_test_is_an_error_named_as_listed():
  found = ctrf.missing(TEST, "the runner exited 4")
  assert (found["name"], found["status"], found["rawStatus"]) == (
    "tests/test_a.py::test_x",
    "failed",
    "error",
  )
  assert found["message"] == f"{ctrf.MISSING} (the runner exited 4)"
  assert found["duration"] == 0


def test_long_messages_and_traces_are_cut():
  case = Case("a", "failed", message="m" * 5000, trace="t" * 50000)
  found = ctrf.entry(case, "p2p", {})
  assert len(str(found["message"])) == ctrf.MESSAGE_LIMIT
  assert len(str(found["trace"])) == ctrf.TRACE_LIMIT


def report() -> dict[str, object]:
  tests = [
    ctrf.entry(Case("a", "passed"), "p2p", {}),
    ctrf.entry(Case("b", "skipped"), "p2p", {}),
    ctrf.entry(Case("c", "passed"), "f2p", {}),
    ctrf.missing(TEST),
  ]
  return ctrf.report(tests, 1000, 1500)


def results(found: dict[str, object]) -> dict[str, object]:
  return cast("dict[str, object]", found["results"])


def test_report_sums_its_tests():
  found = report()
  assert found["reportFormat"] == "CTRF"
  assert found["specVersion"] == ctrf.SPEC_VERSION
  assert results(found)["summary"] == {
    "tests": 4,
    "passed": 2,
    "failed": 1,
    "pending": 0,
    "skipped": 1,
    "other": 0,
    "start": 1000,
    "stop": 1500,
  }


def test_suite_counts_passes_only_passed_tests(tmp_path: Path):
  path = tmp_path / ctrf.FILE
  ctrf.write(report(), path)
  read_back = cast("dict[str, object]", json.loads(path.read_text()))
  assert ctrf.suite_counts(read_back) == {"p2p": (1, 2), "f2p": (1, 2)}


def test_suite_counts_refuses_a_test_without_one_suite():
  found = report()
  tests = cast("list[dict[str, object]]", results(found)["tests"])
  tests[0]["suite"] = ["p2p", "tests/test_a.py"]
  with pytest.raises(ValueError, match="no one suite"):
    _ = ctrf.suite_counts(found)
  with pytest.raises(ValueError, match=r"no results\.tests"):
    _ = ctrf.suite_counts({"results": {}})


def test_write_puts_nothing_over_what_is_there(tmp_path: Path):
  path = tmp_path / ctrf.FILE
  path.symlink_to(tmp_path / "elsewhere")
  with pytest.raises(FileExistsError):
    ctrf.write(report(), path)
  assert not (tmp_path / "elsewhere").exists()

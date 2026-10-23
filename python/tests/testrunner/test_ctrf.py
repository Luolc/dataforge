import json
from pathlib import Path
from typing import cast

import pytest

from dataforge.testrunner import core
from dataforge.testrunner.core import Case
from dataforge.testrunner.core import Test as Listed

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
  assert core.entry(case, TEST.suite, core.labels(TEST)) == {
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
  found = core.entry(Case("t.py::test_y", "passed"), "p2p", core.labels(plain))
  assert "rawStatus" not in found and "message" not in found
  assert found["labels"] == {"id": "p2p::t.py::test_y", "runner": "pytest"}


def test_a_missing_test_is_an_error_named_as_listed():
  found = core.missing(TEST, "the runner exited 4")
  assert (found["name"], found["status"], found["rawStatus"]) == (
    "tests/test_a.py::test_x",
    "failed",
    "error",
  )
  assert found["message"] == f"{core.MISSING} (the runner exited 4)"
  assert found["duration"] == 0


def test_long_messages_and_traces_are_cut():
  case = Case("a", "failed", message="m" * 5000, trace="t" * 50000)
  found = core.entry(case, "p2p", {})
  assert len(str(found["message"])) == core.MESSAGE_LIMIT
  assert len(str(found["trace"])) == core.TRACE_LIMIT


def report() -> dict[str, object]:
  tests = [
    core.entry(Case("a", "passed"), "p2p", {}),
    core.entry(Case("b", "skipped"), "p2p", {}),
    core.entry(Case("c", "passed"), "f2p", {}),
    core.missing(TEST),
  ]
  return core.report(tests, 1000, 1500)


def results(found: dict[str, object]) -> dict[str, object]:
  return cast("dict[str, object]", found["results"])


def test_report_sums_its_tests():
  found = report()
  assert found["reportFormat"] == "CTRF"
  assert found["specVersion"] == core.SPEC_VERSION
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
  path = tmp_path / core.FILE
  core.write(report(), path)
  read_back = cast("dict[str, object]", json.loads(path.read_text()))
  assert core.suite_counts(read_back) == {"p2p": (1, 2), "f2p": (1, 2)}


def test_suite_counts_refuses_a_test_without_one_suite():
  found = report()
  tests = cast("list[dict[str, object]]", results(found)["tests"])
  tests[0]["suite"] = ["p2p", "tests/test_a.py"]
  with pytest.raises(ValueError, match="no one suite"):
    _ = core.suite_counts(found)
  with pytest.raises(ValueError, match=r"no results\.tests"):
    _ = core.suite_counts({"results": {}})


def test_write_puts_nothing_over_what_is_there(tmp_path: Path):
  path = tmp_path / core.FILE
  path.symlink_to(tmp_path / "elsewhere")
  with pytest.raises(FileExistsError):
    core.write(report(), path)
  assert not (tmp_path / "elsewhere").exists()

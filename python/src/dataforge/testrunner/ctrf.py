"""The tests' outcomes as a CTRF report, written to /logs/verifier/ctrf.json.

CTRF (https://ctrf.io/docs/specification/test, 0.1.0) is the format Harbor's
viewer reads at that path. Each test object is one case a runner reported,
with its suite as `suite`, a one-element array, and in `labels` the
reference of the manifest's test it belongs to (`id`), its runner or run
group when it has them, its kind and principle when it has them, and
`expect` for a canary. CTRF has no status for an error: one is `failed` with
`rawStatus` `error`. A test the manifest lists that the runner did not
report is such an error, so that a test kept from running does not pass
unseen. A canary, which must fail on an assertion, counts in no suite; the
report's `results.extra` lists in `canary_broken` each canary that did not,
and in `unlisted` the names a run group reported that the manifest does not
list; with neither, the report has no `extra`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import cast

from .manifest import EXPECT_FAILED, Test

FILE = "ctrf.json"
"""The report's name in the verifier's output directory."""

SPEC_VERSION = "0.1.0"
"""The CTRF version the report follows."""

TOOL = "testrunner"
"""The report's tool."""

ERROR = "error"
"""The rawStatus of a test that errored."""

MISSING = "listed in the manifest but not in the runner's report"
"""The message of a test the runner did not report."""

STATUSES = ("passed", "failed", "skipped")
"""The statuses a case may have."""

EXPECT = "expect"
"""The label of a canary, whose value is the manifest's expect."""

MESSAGE_LIMIT = 2000
TRACE_LIMIT = 20000
"""How many characters of a message and a trace the report keeps."""

TestObject = dict[str, object]
"""A CTRF test object, as JSON."""


@dataclass(frozen=True, slots=True)
class Case:
  """One test case as a runner reported it.

  Attributes:
    name: Its ID, in the runner's form, paths relative to the project.
    status: One of STATUSES.
    duration: How long it ran, in milliseconds.
    error: Whether it errored rather than failed; its status is failed.
    message: Its failure's or skip's message, or None.
    trace: Its failure's trace, or None.
    file: The file that defines it, relative to the project, or None.
  """

  name: str
  status: str
  duration: int = 0
  error: bool = False
  message: str | None = None
  trace: str | None = None
  file: str | None = None


def labels(test: Test) -> dict[str, str]:
  """Return the labels of a test's cases.

  Args:
    test: The manifest's test.

  Returns:
    Its reference, and its runner, run group, kind, principle and expect
    when it has them.
  """
  found = {"id": test.ref}
  if test.runner is not None:
    found["runner"] = test.runner
  if test.run is not None:
    found["run"] = test.run
  if test.kind is not None:
    found["kind"] = test.kind
  if test.principle is not None:
    found["principle"] = test.principle
  if test.expect is not None:
    found[EXPECT] = test.expect
  return found


def entry(case: Case, suite: str, tags: Mapping[str, str]) -> TestObject:
  """Return the CTRF test object of a case.

  Args:
    case: The case.
    suite: The suite it counts in.
    tags: Its labels.

  Returns:
    The test object.
  """
  return plain(case, suite=[suite]) | {"labels": dict(tags)}


def plain(case: Case, **extra: object) -> TestObject:
  """Return the CTRF test object of a case, with no suite or labels.

  Args:
    case: The case.
    **extra: Keys that come after its duration, such as its suite.

  Returns:
    The test object.
  """
  found: TestObject = {
    "name": case.name,
    "status": case.status,
    "duration": case.duration,
    **extra,
  }
  if case.error:
    found["rawStatus"] = ERROR
  if case.message:
    found["message"] = case.message[:MESSAGE_LIMIT]
  if case.trace:
    found["trace"] = case.trace[:TRACE_LIMIT]
  if case.file:
    found["filePath"] = case.file
  return found


def missing(test: Test, why: str = "") -> TestObject:
  """Return the CTRF test object of a test the runner did not report.

  Args:
    test: The manifest's test.
    why: What else is known, such as how the runner exited; or "".

  Returns:
    The test object, an error.
  """
  message = f"{MISSING} ({why})" if why else MISSING
  case = Case(test.id, "failed", error=True, message=message)
  return entry(case, test.suite, labels(test))


def report(
  tests: list[TestObject],
  start: int,
  stop: int,
  *,
  unlisted: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
  """Return the CTRF report of test objects.

  Args:
    tests: The test objects.
    start: When the run started, in milliseconds since the epoch.
    stop: When it stopped, likewise.
    unlisted: For each run group that reported names the manifest does not
      list, `{run, total, names}`, as run.Results gives it.

  Returns:
    The report; its results.extra, when there is one, has canary_broken,
    the references of the canaries with a test object that is not a
    failure of an assertion, and unlisted.
  """
  summary = {
    s: sum(t["status"] == s for t in tests)
    for s in ("passed", "failed", "pending", "skipped", "other")
  }
  results: dict[str, object] = {
    "tool": {"name": TOOL},
    "summary": {"tests": len(tests), **summary, "start": start, "stop": stop},
    "tests": tests,
  }
  extra: dict[str, object] = {}
  broken = canary_broken(tests)
  if broken:
    extra["canary_broken"] = broken
  if unlisted:
    extra["unlisted"] = [dict(u) for u in unlisted]
  if extra:
    results["extra"] = extra
  return {
    "reportFormat": "CTRF",
    "specVersion": SPEC_VERSION,
    "results": results,
  }


def canary_broken(tests: list[TestObject]) -> list[str]:
  """Return the canaries that did not fail on an assertion.

  Args:
    tests: The test objects.

  Returns:
    The reference of each canary with a test object that is not `failed`,
    or is an error, in the order of their first.
  """
  good: dict[str, bool] = {}
  for test in tests:
    tags = _labels(test)
    if tags.get(EXPECT) == EXPECT_FAILED:
      ref = str(tags.get("id"))
      failed = test.get("status") == "failed"
      good[ref] = (
        good.get(ref, True) and failed and test.get("rawStatus") != ERROR
      )
  return [ref for ref, ok in good.items() if not ok]


def _labels(test: Mapping[str, object]) -> dict[str, object]:
  tags = test.get("labels")
  return cast("dict[str, object]", tags) if isinstance(tags, dict) else {}


def suite_counts(report: Mapping[str, object]) -> dict[str, tuple[int, int]]:
  """Return how many tests of each suite passed, and how many there are.

  Args:
    report: A CTRF report, as report gives it or as JSON read back.

  Returns:
    For each suite that has a test, (passed, total); a skipped test does
    not pass, and a canary counts in none.

  Raises:
    ValueError: When the report has no results.tests list, or a test has no
      one-element suite or no status.
  """
  results = report.get("results")
  tests = (
    cast("dict[str, object]", results).get("tests")
    if isinstance(results, dict)
    else None
  )
  if not isinstance(tests, list):
    raise ValueError("the report has no results.tests")
  counts: dict[str, tuple[int, int]] = {}
  for test in cast("list[object]", tests):
    found = cast("dict[str, object]", test) if isinstance(test, dict) else {}
    suite, status = found.get("suite"), found.get("status")
    one = cast("list[object]", suite) if isinstance(suite, list) else []
    if len(one) != 1 or not isinstance(one[0], str):
      raise ValueError(f"a test has no one suite: {found.get('name')!r}")
    if not isinstance(status, str):
      raise ValueError(f"a test has no status: {found.get('name')!r}")
    if _labels(found).get(EXPECT) == EXPECT_FAILED:
      continue
    passed, total = counts.get(one[0], (0, 0))
    counts[one[0]] = (passed + (status == "passed"), total + 1)
  return counts


def write(report: Mapping[str, object], path: Path) -> None:
  """Write a report as a new file.

  Args:
    report: The report.
    path: Where; nothing may be there, not even a link.
  """
  with path.open("x", encoding="utf-8") as f:
    json.dump(report, f, indent=2)

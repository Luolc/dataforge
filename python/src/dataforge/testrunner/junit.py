"""Reading the JUnit XML reports pytest, Maven Surefire and PHPUnit write."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import xml.etree.ElementTree as ET


@dataclass(frozen=True, slots=True)
class Outcome:
  """What a test case's element says of how it ended.

  Attributes:
    status: passed, failed or skipped.
    error: Whether it errored rather than failed.
    message: The message of its failure, error or skip, or None.
    trace: The text of its failure or error, or None.
    duration: How long it ran, in milliseconds.
  """

  status: str
  error: bool
  message: str | None
  trace: str | None
  duration: int


def root(path: Path) -> ET.Element | None:
  """Return a report's root element.

  Args:
    path: The report.

  Returns:
    Its root; None when there is no regular file there or it does not
    parse.
  """
  if path.is_symlink() or not path.is_file():
    return None
  try:
    return ET.parse(path).getroot()
  except ET.ParseError:
    return None


def testcases(path: Path) -> list[ET.Element] | None:
  """Return the testcase elements of a report.

  Args:
    path: The report.

  Returns:
    Its testcase elements, in its order; None when there is no regular file
    there or it does not parse.
  """
  found = root(path)
  return None if found is None else list(found.iter("testcase"))


def outcome(case: ET.Element) -> Outcome:
  """Return how a test case ended.

  Args:
    case: Its testcase element.

  Returns:
    Its outcome: a failure or an error comes before a skip.
  """
  for tag, status, error in (
    ("failure", "failed", False),
    ("error", "failed", True),
    ("skipped", "skipped", False),
  ):
    found = case.find(tag)
    if found is not None:
      message = found.get("message") or None
      trace = (found.text or "").strip() or None
      return Outcome(status, error, message, trace, _milliseconds(case))
  return Outcome("passed", False, None, None, _milliseconds(case))


def _milliseconds(case: ET.Element) -> int:
  try:
    seconds = float(case.get("time") or 0)
  except ValueError:
    return 0
  return round(seconds * 1000) if math.isfinite(seconds) and seconds > 0 else 0

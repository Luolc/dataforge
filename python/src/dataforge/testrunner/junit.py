"""Reading JUnit XML reports, such as pytest's, Surefire's and PHPUnit's.

A report is read only as UTF-8: one that is not, or whose XML declaration
names another encoding, is no report, so that reading it after the tests
never loads a codec from a disk the tests' code may have changed.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .core import Case, ReportError

_ENCODING = re.compile(rb"""<\?xml[^>]*?encoding\s*=\s*["']([^"']*)["']""")


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
    Its root; None when there is no regular file there, or it is not UTF-8
    or does not parse.
  """
  if path.is_symlink() or not path.is_file():
    return None
  return parse(path.read_bytes())


def parse(data: bytes) -> ET.Element | None:
  """Return the root element of a report's bytes.

  Args:
    data: The report.

  Returns:
    Its root; None when it is not UTF-8, its declaration names another
    encoding, or it does not parse.
  """
  declared = _ENCODING.match(data.removeprefix(b"\xef\xbb\xbf"))
  if declared and declared.group(1).lower() not in (b"utf-8", b"utf8"):
    return None
  try:
    _ = data.decode("utf-8")
  except UnicodeDecodeError:
    return None
  parser = ET.XMLParser(encoding="utf-8")
  try:
    parser.feed(data)
    return parser.close()
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


def marked_outcome(case: ET.Element) -> Outcome:
  """Return how a test case ended, a skip marked by attribute alone too.

  Args:
    case: Its testcase element.

  Returns:
    Its outcome, skipped also when CTest's `status` is notrun or disabled
    or GoogleTest's `result` is skipped or suppressed.
  """
  end = outcome(case)
  if end.status == "passed" and (
    case.get("status") in ("notrun", "disabled")
    or case.get("result") in ("skipped", "suppressed")
  ):
    return Outcome("skipped", False, None, None, end.duration)
  return end


def report_cases(data: bytes) -> list[Case]:
  """Return the cases of a run group's report file of the junit format.

  Args:
    data: The file's bytes.

  Returns:
    A case for each `<testcase>`, in its order, named
    `<classname>::<name>`, or `<name>` with no classname, and skipped also
    when the attributes some runners write say so (marked_outcome).

  Raises:
    ReportError: When it is not UTF-8 JUnit XML, or a testcase has no
      one-line name.
  """
  root = parse(data)
  if root is None:
    raise ReportError("a report is not UTF-8 JUnit XML")
  cases: list[Case] = []
  for tc in root.iter("testcase"):
    classname, name = tc.get("classname") or "", tc.get("name") or ""
    full = f"{classname}::{name}" if classname else name
    if not name or "\n" in full:
      raise ReportError(f"a testcase has no one-line name: {full!r}")
    end = marked_outcome(tc)
    cases.append(
      Case(
        full,
        end.status,
        end.duration,
        end.error,
        end.message,
        end.trace,
        tc.get("file") or None,
      )
    )
  return cases


def _milliseconds(case: ET.Element) -> int:
  try:
    seconds = float(case.get("time") or 0)
  except ValueError:
    return 0
  return round(seconds * 1000) if math.isfinite(seconds) and seconds > 0 else 0

"""The formats of a run group's report that testrunner reads itself.

`junit` is every `*.xml` file under the report's directory, JUnit XML, each
`<testcase>` named `<classname>::<name>`, or `<name>` with no classname, and
skipped also when the attributes some runners write say so
(junit.marked_outcome). `ctrf` is every
`*.json` file, CTRF, of which only each test's `name`, one line, `status`,
one of CTRF's, and `rawStatus` are read: only `passed` passes, `pending` is
read as `skipped` and `other` as `failed`, and `rawStatus` `error` makes a
test an error, so that it never makes a test pass; `message`, `trace`,
`duration` and `filePath` are kept when they have the right type. A file
over LIMIT bytes, not UTF-8, or not of its format's shape makes the whole
report unreadable.

As a command, `python -m testrunner parse <format> <report> <out>` writes a
format's CTRF to out, for a session that tries its run group and for a
parse argv to call.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
from typing import cast

from . import ctrf
from .ctrf import Case
from .manifest import FORMATS

LIMIT = 64 * 1024 * 1024
"""The most bytes a report's file may have."""

SUFFIXES = {"junit": ".xml", "ctrf": ".json"}
"""The suffix of a format's files under a report's directory."""

_STATUSES = {
  "passed": "passed",
  "failed": "failed",
  "skipped": "skipped",
  "pending": "skipped",
  "other": "failed",
}


class ReportError(ValueError):
  """A report that cannot be read."""


def read(format_: str, path: Path) -> list[Case] | None:
  """Return the cases of a report.

  Args:
    format_: One of FORMATS.
    path: The report: a file, or a directory whose files of the format,
      at any depth and in the order of their paths, are read; a link is
      not followed.

  Returns:
    The cases in the files' order, or None when there is no file.

  Raises:
    ReportError: When a file cannot be read.
  """
  files = _files(path, SUFFIXES[format_])
  if not files:
    return None
  cases: list[Case] = []
  for file in files:
    if file.stat().st_size > LIMIT:
      raise ReportError(f"{file.name} is over {LIMIT} bytes")
    data = file.read_bytes()
    cases += _junit(data) if format_ == "junit" else _ctrf(data)
  return cases


def _files(path: Path, suffix: str) -> list[Path]:
  if path.is_symlink():
    return []
  if path.is_file():
    return [path]
  found: list[Path] = []
  for root, dirs, names in os.walk(path):
    dirs.sort()
    found += [
      Path(root, n)
      for n in sorted(names)
      if n.endswith(suffix)
      and not Path(root, n).is_symlink()
      and Path(root, n).is_file()
    ]
  return found


def _junit(data: bytes) -> list[Case]:
  # Imported here: a task's copy has junit.py only when a run group's
  # parse or a runner reads JUnit XML (bundle.files).
  from . import junit

  root = junit.parse(data)
  if root is None:
    raise ReportError("a report is not UTF-8 JUnit XML")
  cases: list[Case] = []
  for tc in root.iter("testcase"):
    classname, name = tc.get("classname") or "", tc.get("name") or ""
    full = f"{classname}::{name}" if classname else name
    if not name or "\n" in full:
      raise ReportError(f"a testcase has no one-line name: {full!r}")
    end = junit.marked_outcome(tc)
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


def _ctrf(data: bytes) -> list[Case]:
  try:
    report = cast("object", json.loads(data.decode("utf-8")))
  except (UnicodeDecodeError, ValueError) as e:
    raise ReportError("a report is not UTF-8 JSON") from e
  results = _dict(report).get("results")
  tests = _dict(results).get("tests")
  if not isinstance(tests, list):
    raise ReportError("a report has no results.tests array")
  return [_case(t) for t in cast("list[object]", tests)]


def _dict(value: object) -> dict[str, object]:
  return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _case(value: object) -> Case:
  test = _dict(value)
  name, status = test.get("name"), test.get("status")
  if not isinstance(name, str) or not name.strip() or "\n" in name:
    raise ReportError(f"a test has no one-line name: {name!r}")
  if not isinstance(status, str) or status not in _STATUSES:
    raise ReportError(f"{name}: status {status!r} is not CTRF's")
  error = test.get("rawStatus") == ctrf.ERROR
  return Case(
    name,
    "failed" if error else _STATUSES[status],
    _duration(test.get("duration")),
    error,
    _text(test.get("message")),
    _text(test.get("trace")),
    _text(test.get("filePath")),
  )


def _duration(value: object) -> int:
  if isinstance(value, bool) or not isinstance(value, int | float):
    return 0
  return round(value) if math.isfinite(value) and value > 0 else 0


def _text(value: object) -> str | None:
  return value if isinstance(value, str) and value else None


def main(argv: list[str]) -> int:
  """Write a format's CTRF.

  Args:
    argv: The format, the report and where to write.

  Returns:
    0 once written; 1 when there is no report or it cannot be read.
  """
  parser = argparse.ArgumentParser(prog="testrunner parse")
  _ = parser.add_argument("format", choices=FORMATS)
  _ = parser.add_argument("report", type=Path)
  _ = parser.add_argument("out", type=Path)
  args = parser.parse_args(argv)
  try:
    cases = read(cast(str, args.format), cast(Path, args.report))
  except ReportError as e:
    print(f"testrunner parse: {e}", file=sys.stderr)
    return 1
  if cases is None:
    print("testrunner parse: no report", file=sys.stderr)
    return 1
  out = cast(Path, args.out)
  if os.path.lexists(out):
    out.unlink()
  ctrf.write(ctrf.report([ctrf.plain(c) for c in cases], 0, 0), out)
  return 0

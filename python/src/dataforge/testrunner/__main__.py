"""Run a task's tests and write /logs/verifier/ctrf.json.

For a dataset with no runner of its own: run as `python -E -s -m testrunner`
in tests/, so that PYTHONPATH, which may name a directory of the project,
does not put a module of the project's before the standard library's in this
process; the runners still get the environment's PYTHONPATH. It writes only
the report; the reward is the dataset's to write, from core.suite_counts. It
restores nothing: its result can be trusted only once the project's
runner_files and the files restore_paths gives for the listed tests are the
base's, which is for whoever runs it to see to. A task's copy has this file
only when the dataset asks for it (bundle.files' main).

As `python -m testrunner parse <format> <report> <out>`, it writes a
format's CTRF to out, for a session that tries its run group and for a
parse argv to call.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import cast

from . import core


def main(argv: list[str] | None = None) -> None:
  """Run the manifest's tests and write the report.

  Args:
    argv: The arguments; None takes the command line's.
  """
  given = sys.argv[1:] if argv is None else argv
  if given[:1] == ["parse"]:
    sys.exit(parse(given[1:]))
  parser = argparse.ArgumentParser(prog="testrunner")
  _ = parser.add_argument("--tests", type=Path, default=Path("/tests"))
  _ = parser.add_argument("--app", type=Path, default=Path("/app"))
  _ = parser.add_argument("--out", type=Path, default=Path("/logs/verifier"))
  _ = parser.add_argument("--base", help="the project's base commit")
  args = parser.parse_args(given)
  tests_dir, app = cast(Path, args.tests), cast(Path, args.app)
  manifest = core.read(tests_dir)
  start = int(time.time() * 1000)
  found = core.run(
    manifest, tests=tests_dir, app=app, base=cast(str | None, args.base)
  )
  stop = int(time.time() * 1000)
  report = core.report(found.tests, start, stop, unlisted=found.unlisted)
  out_dir = cast(Path, args.out)
  out_dir.mkdir(parents=True, exist_ok=True)
  out = out_dir / core.FILE
  if os.path.lexists(out):
    out.unlink()
  core.write(report, out)
  json.dump(core.suite_counts(report), sys.stdout)
  print()


def parse(argv: list[str]) -> int:
  """Write a format's CTRF.

  Args:
    argv: The format, the report and where to write.

  Returns:
    0 once written; 1 when there is no report or it cannot be read.
  """
  parser = argparse.ArgumentParser(prog="testrunner parse")
  _ = parser.add_argument("format", choices=core.FORMATS)
  _ = parser.add_argument("report", type=Path)
  _ = parser.add_argument("out", type=Path)
  args = parser.parse_args(argv)
  try:
    cases = core.read_report(cast(str, args.format), cast(Path, args.report))
  except core.ReportError as e:
    print(f"testrunner parse: {e}", file=sys.stderr)
    return 1
  if cases is None:
    print("testrunner parse: no report", file=sys.stderr)
    return 1
  out = cast(Path, args.out)
  if os.path.lexists(out):
    out.unlink()
  core.write(core.report([core.plain(c) for c in cases], 0, 0), out)
  return 0


if __name__ == "__main__":
  main()

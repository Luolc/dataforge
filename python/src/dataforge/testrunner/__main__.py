"""Run a task's tests and write /logs/verifier/ctrf.json.

For a dataset with no runner of its own: run as `python -m testrunner` with
tests/ on the path. It writes only the report; the reward is the
dataset's to write, from ctrf.suite_counts. It restores nothing: its result
can be trusted only once the project's RUNNER_FILES and the files
restore_paths gives for the listed tests are the base's, which is for
whoever runs it to see to.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import cast

from . import ctrf
from .manifest import read
from .run import run


def main(argv: list[str] | None = None) -> None:
  """Run the manifest's tests and write the report.

  Args:
    argv: The arguments; None takes the command line's.
  """
  parser = argparse.ArgumentParser(prog="testrunner")
  _ = parser.add_argument("--tests", type=Path, default=Path("/tests"))
  _ = parser.add_argument("--app", type=Path, default=Path("/app"))
  _ = parser.add_argument("--out", type=Path, default=Path("/logs/verifier"))
  _ = parser.add_argument("--base", help="the project's base commit")
  args = parser.parse_args(argv)
  tests_dir, app = cast(Path, args.tests), cast(Path, args.app)
  manifest = read(tests_dir)
  start = int(time.time() * 1000)
  found = run(
    manifest, tests=tests_dir, app=app, base=cast(str | None, args.base)
  )
  report = ctrf.report(found, start, int(time.time() * 1000))
  out_dir = cast(Path, args.out)
  out_dir.mkdir(parents=True, exist_ok=True)
  out = out_dir / ctrf.FILE
  if os.path.lexists(out):
    out.unlink()
  ctrf.write(report, out)
  json.dump(ctrf.suite_counts(report), sys.stdout)
  print()


if __name__ == "__main__":
  main()

"""The package's files, for a dataset to copy into a task's tests/."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

PACKAGE = "testrunner"
"""The directory under a task's tests/ the files go in, and the name the
dataset's own runner imports them by."""

COMMON = (
  "__init__.py",
  "__main__.py",
  "adapter.py",
  "bundle.py",
  "ctrf.py",
  "globs.py",
  "junit.py",
  "manifest.py",
  "parse.py",
  "restore.py",
  "run.py",
  "runners.py",
)
"""The files every task needs: the manifest, the run, restore and parse,
junit among them since parse reads the junit format, and runners, which
holds pytest's, Maven's and PHPUnit's adapters and loads the others' only
when they run (runners.load_adapter)."""

NEEDS: dict[str, tuple[str, ...]] = {
  "pytest": ("pytest_main.py",),
  "jest": ("js.py",),
  "vitest": ("js.py",),
  "jasmine": ("js.py", "jasmine_reporter.cjs"),
  "node": ("js.py",),
  "go": ("gotest.py",),
  "cargo": ("cargo.py",),
  "ctest": ("cpp.py",),
  "gtest": ("cpp.py",),
  "maven": (),
  "phpunit": (),
}
"""The files each runner of manifest.RUNNERS needs beyond COMMON."""


def files(runners: Collection[str]) -> dict[str, bytes]:
  """Return the files to copy into a task's tests/.

  Args:
    runners: The runners the task's manifest names (Manifest.runners);
      manifest.RUNNERS gives every file of the package.

  Returns:
    The content of COMMON and of what NEEDS gives for each runner, by its
    path relative to tests/, such as testrunner/manifest.py, sorted.

  Raises:
    ValueError: When a runner is not one of manifest.RUNNERS.
  """
  unknown = sorted(set(runners) - NEEDS.keys())
  if unknown:
    raise ValueError(f"unknown runners: {', '.join(unknown)}")
  names = {*COMMON, *(n for r in runners for n in NEEDS[r])}
  here = Path(__file__).parent
  return {f"{PACKAGE}/{n}": (here / n).read_bytes() for n in sorted(names)}

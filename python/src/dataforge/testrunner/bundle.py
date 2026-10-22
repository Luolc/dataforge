"""The package's files, for a dataset to copy into a task's tests/."""

from __future__ import annotations

from pathlib import Path

PACKAGE = "testrunner"
"""The directory under a task's tests/ the files go in, and the name the
dataset's own runner imports them by."""


def files() -> dict[str, bytes]:
  """Return the files to copy into a task's tests/.

  Returns:
    The content of each, by its path relative to tests/, such as
    testrunner/manifest.py.
  """
  here = Path(__file__).parent
  return {
    f"{PACKAGE}/{p.relative_to(here).as_posix()}": p.read_bytes()
    for p in sorted(here.rglob("*.py"))
    if "__pycache__" not in p.parts
  }

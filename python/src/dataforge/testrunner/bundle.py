"""The package's files, for a dataset to copy into a task's tests/."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from .manifest import Manifest
from .registry import MODULES
from .runners import spec

PACKAGE = "testrunner"
"""The directory under a task's tests/ the files go in, and the name the
dataset's own runner imports them by."""

COMMON = (
  "__init__.py",
  "__main__.py",
  "adapter.py",
  "bundle.py",
  "ctrf.py",
  "deps.py",
  "globs.py",
  "manifest.py",
  "parse.py",
  "registry.py",
  "restore.py",
  "run.py",
  "runners.py",
)
"""The files every task needs: the manifest, the run, restore and parse, the
check of where dependencies come from (deps), which guards every task
whatever its runners, and the registry of the runners, which imports a
runner's module only when it is asked for (runners.spec)."""

FORMAT_FILES: dict[str, tuple[str, ...]] = {"junit": ("junit.py",), "ctrf": ()}
"""The files parse needs for each of manifest.FORMATS."""


def files(
  runners: Collection[str], formats: Collection[str]
) -> dict[str, bytes]:
  """Return the files to copy into a task's tests/.

  Args:
    runners: The runners the task's manifest names (Manifest.runners);
      manifest.RUNNERS gives every runner's.
    formats: The formats its run groups' parse names (Manifest.formats);
      manifest.FORMATS gives every format's.

  Returns:
    The content of COMMON, of each runner's module and of the files its
    Spec.files names, and of FORMAT_FILES' for each format, by its path
    relative to tests/, such as testrunner/manifest.py, sorted.

  Raises:
    ValueError: When a runner is not one of manifest.RUNNERS or a format
      not one of manifest.FORMATS.
  """
  unknown = sorted(
    {*(set(runners) - MODULES.keys()), *(set(formats) - FORMAT_FILES.keys())}
  )
  if unknown:
    raise ValueError(f"unknown runners or formats: {', '.join(unknown)}")
  names = {*COMMON, *(n for f in formats for n in FORMAT_FILES[f])}
  for runner in runners:
    names |= {f"{MODULES[runner]}.py", *spec(runner).files}
  here = Path(__file__).parent
  return {f"{PACKAGE}/{n}": (here / n).read_bytes() for n in sorted(names)}


def files_for(manifest: Manifest) -> dict[str, bytes]:
  """Return the files to copy into the tests/ of a task with a manifest.

  Args:
    manifest: The task's manifest.

  Returns:
    files of its runners and formats.
  """
  return files(manifest.runners, manifest.formats)

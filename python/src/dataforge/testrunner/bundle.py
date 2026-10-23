"""The package's files, for a dataset to copy into a task's tests/.

This module is only the host's: a task's copy never has it.
"""

from __future__ import annotations

import ast
from collections.abc import Collection
import json
from pathlib import Path

from .core import Manifest, spec
from .registry import MODULES

PACKAGE = "testrunner"
"""The directory under a task's tests/ the files go in, and the name the
dataset's own runner imports them by."""

COMMON = ("__init__.py", "core.py", "deps.py", "registry.py")
"""The files every task needs: the core, the check of where dependencies
come from (deps), which guards every task whatever its runners, and the
registry of the runners, which imports a runner's module only when it is
asked for (core.spec) and in a task's copy names only its runners
(registry)."""

MAIN = "__main__.py"
"""The file that runs a task's tests as `python -m testrunner`, for a
dataset with no runner of its own, which asks for it (files' main)."""

FORMAT_FILES: dict[str, tuple[str, ...]] = {"junit": ("junit.py",), "ctrf": ()}
"""The files parse needs for each of core.FORMATS."""


def files(
  runners: Collection[str], formats: Collection[str], *, main: bool = False
) -> dict[str, bytes]:
  """Return the files to copy into a task's tests/.

  Args:
    runners: The runners the task's manifest names (Manifest.runners);
      core.RUNNERS gives every runner's.
    formats: The formats its run groups' parse names (Manifest.formats);
      core.FORMATS gives every format's.
    main: Whether to add MAIN, for a dataset with no runner of its own.

  Returns:
    The content of COMMON, registry.py's as registry gives it for the
    runners, of each runner's module and of the files its Spec.files names,
    of FORMAT_FILES' for each format, and of MAIN when asked for, by its
    path relative to tests/, such as testrunner/core.py, sorted.

  Raises:
    ValueError: When a runner is not one of core.RUNNERS or a format not
      one of core.FORMATS.
  """
  unknown = sorted(
    {*(set(runners) - MODULES.keys()), *(set(formats) - FORMAT_FILES.keys())}
  )
  if unknown:
    raise ValueError(f"unknown runners or formats: {', '.join(unknown)}")
  names = {*COMMON, *(n for f in formats for n in FORMAT_FILES[f])}
  if main:
    names.add(MAIN)
  for runner in runners:
    names |= {f"{MODULES[runner]}.py", *spec(runner).files}
  here = Path(__file__).parent
  found = {f"{PACKAGE}/{n}": (here / n).read_bytes() for n in sorted(names)}
  found[f"{PACKAGE}/registry.py"] = registry(runners)
  return found


def files_for(manifest: Manifest) -> dict[str, bytes]:
  """Return the files to copy into the tests/ of a task with a manifest.

  Args:
    manifest: The task's manifest.

  Returns:
    files of its runners and formats, without MAIN.
  """
  return files(manifest.runners, manifest.formats)


def registry(runners: Collection[str]) -> bytes:
  """Return the registry.py of a task's copy.

  A task's copy names only its own runners, so that it holds no other
  runner's name.

  Args:
    runners: The task's runners, each one of core.RUNNERS.

  Returns:
    The package's registry.py with its MODULES only the runners', in
    MODULES' order, as a dict literal.
  """
  data = (Path(__file__).parent / "registry.py").read_bytes()
  value = next(
    n.value
    for n in ast.parse(data).body
    if isinstance(n, ast.AnnAssign)
    and isinstance(n.target, ast.Name)
    and n.target.id == "MODULES"
    and n.value is not None
  )
  lines = data.splitlines(keepends=True)
  start = sum(map(len, lines[: value.lineno - 1])) + value.col_offset
  end = sum(map(len, lines[: (value.end_lineno or 0) - 1]))
  end += value.end_col_offset or 0
  chosen = [
    f"  {json.dumps(r)}: {json.dumps(m)},\n"
    for r, m in MODULES.items()
    if r in runners
  ]
  literal = "{\n" + "".join(chosen) + "}" if chosen else "{}"
  return data[:start] + literal.encode() + data[end:]

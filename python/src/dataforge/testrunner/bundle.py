"""The package's files, for a dataset to copy into a task's tests/.

This module is only the host's: a task's copy never has it.
"""

from __future__ import annotations

import ast
from collections.abc import Collection
import importlib
import json
from pathlib import Path
from typing import cast

from .core import Manifest, Spec, spec
from .registry import GUARDS, MODULES

PACKAGE = "testrunner"
"""The directory under a task's tests/ the files are copied to, and the name
the dataset's own runner imports them by."""

COMMON = ("__init__.py", "core.py", "deps.py", "registry.py")
"""The files every task needs: the core, the checks of where dependencies
come from (deps), which guard every task whatever its runners and
languages, and the registry of the runners and the modules of GUARDS,
which imports a module only when it is asked for (core.spec, core.guards)
and in a task's copy names only the task's (registry)."""

MAIN = "__main__.py"
"""The file that runs a task's tests as `python -m testrunner`, for a
dataset with no runner of its own, which asks for it (files' main)."""

FORMAT_FILES: dict[str, tuple[str, ...]] = {"junit": ("junit.py",), "ctrf": ()}
"""The files parse needs for each of core.FORMATS."""


def guards_for(runners: Collection[str], paths: Collection[str]) -> list[str]:
  """Return the modules of GUARDS whose checks a task's copy needs.

  A module's checks can change the outcome only of the tests of its
  runners or of tests in its language, which are in the project when the
  task is written; so a module is needed when the task's runners use it or
  one of the project's files is in its language, and none other is.

  Args:
    runners: The runners the task's manifest names (Manifest.runners).
    paths: The paths of the project's files known when the task is
      written, relative to the project.

  Returns:
    Each module of GUARDS that holds the adapter of one of runners, or one
    of whose SOURCES, the suffixes of its language's files, ends one of
    paths, in GUARDS' order.
  """
  used = {MODULES[r] for r in runners if r in MODULES}
  found: list[str] = []
  for name in GUARDS:
    module = importlib.import_module(f".{name}", __package__)
    sources = tuple(getattr(module, "SOURCES", ()))
    if name in used or any(p.endswith(sources) for p in paths):
      found.append(name)
  return found


def files(
  runners: Collection[str],
  formats: Collection[str],
  *,
  main: bool = False,
  guards: Collection[str] = (),
) -> dict[str, bytes]:
  """Return the files to copy into a task's tests/.

  Args:
    runners: The runners the task's manifest names (Manifest.runners);
      core.RUNNERS gives every runner's.
    formats: The formats its run groups' parse names (Manifest.formats);
      core.FORMATS gives every format's.
    main: Whether to add MAIN, for a dataset with no runner of its own.
    guards: Modules of GUARDS to add beyond the runners' (guards_for).

  Returns:
    The content of COMMON, registry.py's as registry gives it for the
    runners and guards, of each runner's module and of guards, of the
    files each of their runners' Spec.files names, of FORMAT_FILES' for
    each format, and of MAIN when asked for, by its path relative to
    tests/, such as testrunner/core.py, sorted.

  Raises:
    ValueError: When a runner is not one of core.RUNNERS, a format not one
      of core.FORMATS or a guard not one of GUARDS.
  """
  unknown = sorted(
    {
      *(set(runners) - MODULES.keys()),
      *(set(formats) - FORMAT_FILES.keys()),
      *(set(guards) - set(GUARDS)),
    }
  )
  if unknown:
    raise ValueError(
      f"unknown runners, formats or guards: {', '.join(unknown)}"
    )
  names = {*COMMON, *(n for f in formats for n in FORMAT_FILES[f])}
  if main:
    names.add(MAIN)
  for runner in runners:
    names |= {f"{MODULES[runner]}.py", *spec(runner).files}
  for name in guards:
    module = importlib.import_module(f".{name}", __package__)
    specs = cast("dict[str, Spec]", getattr(module, "SPECS", {}))
    names |= {f"{name}.py", *(n for s in specs.values() for n in s.files)}
  here = Path(__file__).parent
  found = {f"{PACKAGE}/{n}": (here / n).read_bytes() for n in sorted(names)}
  found[f"{PACKAGE}/registry.py"] = registry(runners, guards)
  return found


def files_for(manifest: Manifest, paths: Collection[str]) -> dict[str, bytes]:
  """Return the files to copy into the tests/ of a task with a manifest.

  Args:
    manifest: The task's manifest.
    paths: The paths of the project's files known when the task is
      written (guards_for).

  Returns:
    files of its runners, formats and guards_for, without MAIN.
  """
  return files(
    manifest.runners,
    manifest.formats,
    guards=guards_for(manifest.runners, paths),
  )


def registry(runners: Collection[str], guards: Collection[str] = ()) -> bytes:
  """Return the registry.py of a task's copy.

  A task's copy names only its own runners and the modules it has, so
  that it holds no other runner's name.

  Args:
    runners: The task's runners, each one of core.RUNNERS.
    guards: Modules of GUARDS it has beyond its runners'.

  Returns:
    The package's registry.py with its MODULES only the runners', in
    MODULES' order, as a dict literal, and its GUARDS only those of its
    runners' modules and of guards, in GUARDS' order, as a tuple literal.
  """
  data = (Path(__file__).parent / "registry.py").read_bytes()
  modules = [
    f"  {json.dumps(r)}: {json.dumps(m)},\n"
    for r, m in MODULES.items()
    if r in runners
  ]
  had = {MODULES[r] for r in runners} | set(guards)
  kept = [json.dumps(m) for m in GUARDS if m in had]
  data = _replace(
    data, "GUARDS", f"({', '.join(kept)}{',' * (len(kept) == 1)})"
  )
  return _replace(
    data, "MODULES", "{\n" + "".join(modules) + "}" if modules else "{}"
  )


def guards_of(data: bytes) -> list[str]:
  """Return the GUARDS a copy's registry.py names.

  With the manifest, they give what files gives that copy, for a dataset
  that no longer has the project's files to check a task's copy against.

  Args:
    data: The copy's registry.py.

  Returns:
    Its GUARDS, in their order.

  Raises:
    ValueError: When it names one that is not of GUARDS, or names no
      GUARDS, as a copy of the old layout, written before a copy held the
      checks of a fix's files, does not.
  """
  value = _value(data, "GUARDS")
  if value is None:
    raise ValueError(
      "registry.py names no GUARDS: a copy of the old layout, written"
      + " before a copy held the checks of a fix's files"
    )
  found = cast("tuple[str, ...]", ast.literal_eval(value))
  if unknown := sorted(set(found) - set(GUARDS)):
    raise ValueError(f"unknown guards: {', '.join(unknown)}")
  return list(found)


def _value(data: bytes, name: str) -> ast.expr | None:
  # The value of registry.py's annotated assignment to name, if it has one.
  return next(
    (
      n.value
      for n in ast.parse(data).body
      if isinstance(n, ast.AnnAssign)
      and isinstance(n.target, ast.Name)
      and n.target.id == name
      and n.value is not None
    ),
    None,
  )


def _replace(data: bytes, name: str, literal: str) -> bytes:
  # registry.py with the value of its annotated assignment to name replaced.
  value = _value(data, name)
  assert value is not None, f"the package's registry.py names no {name}"
  lines = data.splitlines(keepends=True)
  start = sum(map(len, lines[: value.lineno - 1])) + value.col_offset
  end = sum(map(len, lines[: (value.end_lineno or 0) - 1]))
  end += value.end_col_offset or 0
  return data[:start] + literal.encode() + data[end:]

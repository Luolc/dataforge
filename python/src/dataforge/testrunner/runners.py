"""Each runner's adapter and data, from its own module once it is asked for.

registry.MODULES names the module of each runner, whose SPECS gives the
runner's Spec. A task's copy may hold the modules of only the runners its
manifest names (bundle.files), so a runner's module is imported only when
its adapter or data is asked for. The check of the build files that say
where dependencies come from is no runner's (deps).

A runner is started once for each suite and directory, on the tests of the
manifest that name it there, and writes its report outside the project. A
file that only the runner loads by itself (runner_files) is for the dataset
to check back to the project's base before the tests run. A build file that
also holds the runner's configuration, which a fix may need to change, is
not: its configuration is taken from the base and given to the runner on the
command line, where the runner allows it, and a test the runner then leaves
out is an error all the same (ctrf.missing); where no command line replaces
it (build_config), the dataset keeps it at the base. A project file that the
base's configuration names to run in the test process, such as a plugin or a
setup file, is neither: it is the dataset's to restore.
"""

from __future__ import annotations

from collections.abc import Collection
import fnmatch
import importlib
from typing import cast

from .adapter import Runner, Spec
from .deps import dependency_file, dependency_overrides
from .manifest import Test
from .registry import MODULES

__all__ = [
  "build_config",
  "dependency_file",
  "dependency_overrides",
  "id_file",
  "is_build_config",
  "load_adapter",
  "loaded_by_runner",
  "restore_paths",
  "runner_files",
  "spec",
]


def spec(runner: str) -> Spec:
  """Return a runner's Spec, importing its module.

  Args:
    runner: A runner of registry.MODULES.

  Returns:
    The Spec its module's SPECS gives.

  Raises:
    ValueError: When it is not a runner of registry.MODULES; each function
      here that takes a runner raises it so.
  """
  if runner not in MODULES:
    raise ValueError(f"unknown runner: {runner}")
  module = importlib.import_module(f".{MODULES[runner]}", __package__)
  return cast("dict[str, Spec]", module.SPECS)[runner]


def load_adapter(runner: str) -> type[Runner]:
  """Return a runner's adapter.

  Args:
    runner: The runner.

  Returns:
    The adapter's class.
  """
  return spec(runner).adapter


def runner_files(runner: str) -> tuple[str, ...]:
  """Return the patterns of the files a runner loads by itself.

  Args:
    runner: The runner.

  Returns:
    Spec.runner_files: a pattern without `/` matches a file's name, one
    with `/` the end of its path.
  """
  return spec(runner).runner_files


def loaded_by_runner(runner: str, path: str) -> bool:
  """Return whether a runner loads a file by itself.

  Args:
    runner: The runner.
    path: The file's path, relative to the project.

  Returns:
    Whether one of its runner_files matches it.
  """
  return _matches(runner_files(runner), path)


def build_config(runner: str) -> tuple[str, ...]:
  """Return the patterns of a runner's build files that a dataset keeps.

  Args:
    runner: The runner.

  Returns:
    Spec.build_config, patterns as runner_files' are: the build files that
    also hold its test configuration and that no command line replaces.
  """
  return spec(runner).build_config


def is_build_config(runner: str, path: str) -> bool:
  """Return whether a file is one of a runner's build_config.

  Args:
    runner: The runner.
    path: The file's path, relative to the project.

  Returns:
    Whether one of its build_config matches it.
  """
  return _matches(build_config(runner), path)


def id_file(runner: str, id_: str) -> str | None:
  """Return the file a test's ID names.

  Args:
    runner: The test's runner.
    id_: Its ID.

  Returns:
    The ID up to the runner's Spec.id_sep, a path relative to the project;
    None when the runner's IDs name no file.
  """
  sep = spec(runner).id_sep
  return None if sep is None else id_.split(sep, 1)[0]


def restore_paths(test: Test, files: Collection[str]) -> list[str]:
  """Return the files of the base that define a test.

  Args:
    test: The test.
    files: The paths, relative to the project, of the base's files.

  Returns:
    Those of files that hold the test's code, as its runner's
    Spec.restore_paths gives them; without one, its ID's file when files
    has it; none for a test of a run group with a command of its own.
  """
  if test.runner is None:
    return []
  found = spec(test.runner)
  if found.restore_paths is not None:
    return found.restore_paths(test, files)
  path = id_file(test.runner, test.id)
  return [path] if path is not None and path in files else []


def _matches(patterns: tuple[str, ...], path: str) -> bool:
  # A pattern without `/` matches a file's name, one with `/` the end of its
  # path.
  name = path.rsplit("/", 1)[-1]
  for pattern in patterns:
    if "/" in pattern:
      if path == pattern or path.endswith("/" + pattern):
        return True
    elif fnmatch.fnmatchcase(name, pattern):
      return True
  return False

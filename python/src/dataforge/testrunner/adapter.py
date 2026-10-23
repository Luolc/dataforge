"""What every runner's adapter is given and gives back."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
from typing import Protocol

from .ctrf import Case
from .manifest import Test

PREFIX = ".testrunner-"
"""The start of the name of each file testrunner writes into the project for
a run, and removes once it ends."""


@dataclass(frozen=True, slots=True)
class Context:
  """Where a runner runs.

  Attributes:
    app: The project, an absolute path.
    dir: The directory the runner starts in, inside the project.
    work: An empty directory outside the project for the runner's report.
    base: The project's base commit, whose files the configuration is taken
      from, or None to take them as they are.
  """

  app: Path
  dir: Path
  work: Path
  base: str | None

  def base_text(self, path: Path) -> str | None:
    """Return the text a file of the project has at the base.

    Args:
      path: The file, an absolute path inside the project.

    Returns:
      Its text, or None when it has none there.
    """
    rel = path.relative_to(self.app).as_posix()
    if self.base is None:
      if path.is_symlink() or not path.is_file():
        return None
      return path.read_text(encoding="utf-8", errors="replace")
    done = subprocess.run(
      [
        "git",
        "--no-replace-objects",
        "-C",
        str(self.app),
        "cat-file",
        "blob",
        f"{self.base}:{rel}",
      ],
      capture_output=True,
      check=False,
      env=_git_env(),
    )
    if done.returncode != 0:
      return None
    return done.stdout.decode("utf-8", errors="replace")


def _git_env() -> dict[str, str]:
  # Only the repository's own configuration.
  return {
    **os.environ,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
  }


@dataclass(frozen=True, slots=True)
class Call:
  """One command of a run.

  Attributes:
    argv: The command.
    env: What it adds to the environment.
    output: A file outside the project that its standard output and error
      go to, together, for the adapter to read as its report; None leaves
      them as they are.
  """

  argv: list[str]
  env: Mapping[str, str] = field(default_factory=dict[str, str])
  output: Path | None = None


class Runner(Protocol):
  """A runner's adapter."""

  def prepare(self, ctx: Context, tests: list[Test]) -> list[Path]:
    """Ready the project for a run.

    Args:
      ctx: Where it runs.
      tests: The tests it runs.

    Returns:
      The files it wrote into the project, which go once the run ends.
    """
    ...

  def commands(self, ctx: Context, tests: list[Test]) -> list[Call]:
    """Return the commands that run tests, one after another.

    Args:
      ctx: Where it runs.
      tests: The tests to run.

    Returns:
      The commands, run in ctx.dir; none when none of the tests is there to
      run.
    """
    ...

  def cases(self, ctx: Context) -> list[Case] | None:
    """Return the cases of the report a run wrote.

    Args:
      ctx: Where it ran.

    Returns:
      The cases, or None when there is no report.
    """
    ...

  def covers(self, outer: str, inner: str) -> bool:
    """Return whether one ID names a case of the test another names.

    Args:
      outer: An ID, such as a test's.
      inner: Another, such as a case's.

    Returns:
      Whether inner is outer, or one of its cases, such as a parameter or a
      method of a class.
    """
    ...


def rel(ctx: Context, path: Path) -> str:
  """Return a path relative to the project, `/`-separated.

  Args:
    ctx: Where the runner runs.
    path: The path.

  Returns:
    The path from the project's root.
  """
  return Path(os.path.relpath(path, ctx.app)).as_posix()


def write_new(path: Path, text: str) -> Path:
  """Write a file, after removing what is at its name, a link not followed.

  Args:
    path: The file.
    text: Its text.

  Returns:
    The path.
  """
  if os.path.lexists(path):
    path.unlink()
  with path.open("x", encoding="utf-8") as f:
    _ = f.write(text)
  return path


def upward(ctx: Context) -> list[Path]:
  """Return the directories from where the runner starts up to the root.

  Args:
    ctx: Where the runner runs.

  Returns:
    ctx.dir, then each of its parents inside the project, the root last.
  """
  found = [ctx.dir]
  for here in ctx.dir.parents:
    if here != ctx.app and ctx.app not in here.parents:
      break
    found.append(here)
  return found


@dataclass(frozen=True, slots=True)
class Spec:
  """What testrunner knows of a runner, which its module's SPECS gives.

  Attributes:
    adapter: Its adapter's class.
    runner_files: The files it loads by itself that hold only its own
      configuration, which a dataset checks back to the base before the
      tests run: a pattern without `/` matches a file's name, one with `/`
      the end of its path.
    build_config: The build files that also hold its test configuration and
      that no command line replaces, which a dataset keeps at the base;
      patterns as runner_files' are.
    id_sep: What ends the file an ID names, or None when an ID names none.
    restore_paths: What gives, of the base's files, those that define a
      test (runners.restore_paths); None gives its ID's file when the base
      has it.
    files: The package's files it needs beyond its module and the common
      ones (bundle.files).
  """

  adapter: type[Runner]
  runner_files: tuple[str, ...] = ()
  build_config: tuple[str, ...] = ()
  id_sep: str | None = None
  restore_paths: Callable[[Test, Collection[str]], list[str]] | None = None
  files: tuple[str, ...] = ()

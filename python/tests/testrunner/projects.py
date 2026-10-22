"""Small synthetic projects for testrunner's tests."""

import os
from pathlib import Path
import subprocess

from dataforge.testrunner.runners import Context

GIT_ENV = {
  **os.environ,
  "GIT_AUTHOR_NAME": "Example",
  "GIT_AUTHOR_EMAIL": "example@example.com",
  "GIT_COMMITTER_NAME": "Example",
  "GIT_COMMITTER_EMAIL": "example@example.com",
  "GIT_CONFIG_NOSYSTEM": "1",
  "GIT_CONFIG_GLOBAL": "/dev/null",
}


def write(root: Path, files: dict[str, str]) -> None:
  for name, text in files.items():
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text)


# Writes files into a new repository and returns its one commit.
def commit(app: Path, files: dict[str, str]) -> str:
  write(app, files)
  for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
    _ = subprocess.run(["git", "-C", str(app), *args], check=True, env=GIT_ENV)
  done = subprocess.run(
    ["git", "-C", str(app), "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
    env=GIT_ENV,
  )
  return done.stdout.strip()


# The context of a run in tmp_path/app, its work in tmp_path/work.
def context(
  tmp_path: Path, where: str = ".", base: str | None = None
) -> Context:
  app = (tmp_path / "app").resolve()
  app.mkdir(exist_ok=True)
  work = tmp_path / "work"
  work.mkdir(exist_ok=True)
  return Context(app, app / where, work, base)

import ast
from collections.abc import Generator
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time
from typing import cast

from fastapi.testclient import TestClient
import pytest

import dataforge.viz
from dataforge.viz.app import create_app
from dataforge.viz.config import load_site
from dataforge.viz.live import held_locks
from tests.viz.sites import FIXTURES, make_tree, PROJECT, Tree

ATTEMPTS = "/api/projects/example/attempts"


def fixture() -> dict[str, object]:
  return cast(
    dict[str, object], json.loads((FIXTURES / "record.json").read_text())
  )


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
  tree = make_tree(tmp_path)
  _ = tree.project.write_text(PROJECT + "lock: .lock\n")
  record = fixture()
  # Two attempts of batch-1 that have not ended, task-2 the newer, and one
  # that has.
  for name, status, age in (("task-1", "passed", 30), ("task-0", None, 20)):
    write(tree, name, record | {"status": status}, age)
  write(tree, "task-2", record | {"status": None}, 10)
  _ = (tree.root / "batch-1" / ".lock").write_text("")
  return tree


def write(tree: Tree, name: str, record: object, age: int) -> None:
  path = tree.root / "batch-1" / name / "record.json"
  path.parent.mkdir(exist_ok=True)
  _ = path.write_text(json.dumps(record))
  then = time.time() - age
  os.utime(path, (then, then))


def live(tree: Tree, static: Path) -> dict[str, object]:
  web = TestClient(create_app(load_site(tree.site), static))
  rows = cast(list[dict[str, object]], web.get(ATTEMPTS).json())
  return {str(r["id"]): r["live"] for r in rows}


@pytest.fixture
def static(tmp_path: Path) -> Path:
  dist = tmp_path / "dist"
  dist.mkdir()
  _ = (dist / "index.html").write_text("<!doctype html>")
  return dist


@contextmanager
def holding(path: Path, posix: bool = False) -> Generator[None]:
  with path.open("a") as f:
    if posix:
      fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
      fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield


@pytest.mark.parametrize("posix", [False, True])
def test_a_held_lock_is_found(tree: Tree, static: Path, posix: bool) -> None:
  with holding(tree.root / "batch-1" / ".lock", posix):
    found = live(tree, static)

  assert found == {
    "batch-1/task-2": "running",
    "batch-1/task-0": "may have stopped",
    "batch-1/task-1": None,
  }


def test_a_free_lock_is_not(tree: Tree, static: Path) -> None:
  # Another file held, so that /proc/locks is not empty.
  with holding(tree.root / "batch-1" / "other.lock"):
    found = live(tree, static)

  assert found == {
    "batch-1/task-2": "may have stopped",
    "batch-1/task-0": "may have stopped",
    "batch-1/task-1": None,
  }


def test_a_symlinked_lock_is_never_followed(tree: Tree, static: Path) -> None:
  lock = tree.root / "batch-1" / ".lock"
  target = tree.root / "held.lock"
  lock.unlink()
  lock.symlink_to(target)

  with holding(target):
    found = live(tree, static)

  assert found["batch-1/task-2"] == "may have stopped"


def test_without_a_lock_an_attempt_is_only_not_ended(
  tree: Tree, static: Path
) -> None:
  _ = tree.project.write_text(PROJECT)

  with holding(tree.root / "batch-1" / ".lock"):
    found = live(tree, static)

  assert found["batch-1/task-2"] == "not ended"
  assert found["batch-1/task-1"] is None


def test_not_ended_names_the_words_of_an_attempt_that_runs(
  tree: Tree, static: Path
) -> None:
  _ = tree.project.write_text(PROJECT + "not_ended: [running]\n")
  record = fixture()
  write(tree, "task-3", record | {"status": "running"}, 5)

  found = live(tree, static)

  assert found["batch-1/task-3"] == "not ended"
  assert found["batch-1/task-2"] is None


def test_a_raised_attempt_has_ended(tree: Tree, static: Path) -> None:
  record = fixture()
  visits = cast(list[dict[str, object]], record["visits"])
  visits[-1]["outcome"] = "raised"
  write(tree, "task-2", record | {"status": None}, 10)

  found = live(tree, static)

  assert found["batch-1/task-2"] is None


def test_proc_locks_is_read_by_device_and_inode() -> None:
  text = (
    "1: FLOCK  ADVISORY  WRITE 4242 103:01:3152422 0 EOF\n"
    + "1: -> FLOCK  ADVISORY  WRITE 4343 103:01:77 0 EOF\n"
    + "2: POSIX  ADVISORY  READ 4444 00:1d:149 128 128\n"
  )

  assert held_locks(text) == {(0x103, 0x01, 3152422), (0x00, 0x1D, 149)}


def test_the_backend_never_takes_a_lock(
  tree: Tree, static: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  package = Path(dataforge.viz.__file__).parent
  names: set[str] = set()
  for source in package.rglob("*.py"):
    for node in ast.walk(ast.parse(source.read_text())):
      if isinstance(node, ast.Import | ast.ImportFrom):
        names |= {a.name for a in node.names}
        names.add(getattr(node, "module", None) or "")
      elif isinstance(node, ast.Attribute):
        names.add(node.attr)
      elif isinstance(node, ast.Name):
        names.add(node.id)

  lock = tree.root / "batch-1" / ".lock"
  with holding(lock):

    def refuse(*args: object) -> None:
      raise AssertionError(f"a lock was taken: {args}")

    for name in ("flock", "lockf", "fcntl"):
      monkeypatch.setattr(fcntl, name, refuse)
    found = live(tree, static)

  assert not names & {"fcntl", "flock", "lockf"}
  assert found["batch-1/task-2"] == "running"

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


def test_an_unreadable_record_is_no_items_newest(
  tree: Tree, static: Path
) -> None:
  newest = tree.root / "batch-1" / "task-3" / "record.json"
  newest.parent.mkdir()
  _ = newest.write_text("{not json")

  with holding(tree.root / "batch-1" / ".lock"):
    found = live(tree, static)

  assert found["batch-1/task-3"] is None
  assert found["batch-1/task-2"] == "running"
  with holding(tree.root / "batch-1" / ".lock"):
    held_for = holders(tree, static)
  assert held_for["batch-1/task-3"] == (None, False)


def held(tree: Tree, static: Path) -> dict[str, object]:
  web = TestClient(create_app(load_site(tree.site), static))
  rows = cast(list[dict[str, object]], web.get(ATTEMPTS).json())
  return {str(r["id"]): r["held"] for r in rows}


def test_every_row_says_whether_its_items_lock_is_held(
  tree: Tree, static: Path
) -> None:
  unreadable = tree.root / "batch-1" / "task-3"
  unreadable.mkdir()

  with holding(tree.root / "batch-1" / ".lock"):
    found = held(tree, static)
  free = held(tree, static)

  assert found == dict.fromkeys(
    ("batch-1/task-0", "batch-1/task-1", "batch-1/task-2", "batch-1/task-3"),
    True,
  )
  assert set(free.values()) == {False}


def test_without_a_lock_held_is_null(tree: Tree, static: Path) -> None:
  _ = tree.project.write_text(PROJECT)

  with holding(tree.root / "batch-1" / ".lock"):
    found = held(tree, static)

  assert set(found.values()) == {None}


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


@pytest.mark.parametrize("outcome", ["raised", "interrupted"])
def test_a_raised_attempt_has_ended(
  tree: Tree, static: Path, outcome: str
) -> None:
  record = fixture()
  visits = cast(list[dict[str, object]], record["visits"])
  visits[-1]["outcome"] = outcome
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


LIVE = "/api/projects/example/live"


def two_states() -> dict[str, object]:
  # The fixture's record, not ended, with a second state that write's done
  # goes to.
  record = fixture() | {"status": None}
  graph = cast(dict[str, object], record["graph"])
  edges = cast(list[dict[str, object]], graph["edges"])
  edges[0] |= {"to": "check", "end": None}
  edges.append(
    {"from": "check", "outcome": "pass", "to": None, "end": "passed"}
  )
  return record


def state_of(tree: Tree, static: Path, name: str) -> dict[str, object]:
  web = TestClient(create_app(load_site(tree.site), static))
  response = web.get(LIVE, params={"attempt": f"batch-1/{name}"})
  assert response.status_code == 200
  return cast(dict[str, object], response.json())


def test_a_running_attempt_is_in_the_state_its_last_visit_goes_to(
  tree: Tree, static: Path
) -> None:
  write(tree, "task-2", two_states(), 10)

  with holding(tree.root / "batch-1" / ".lock"):
    found = state_of(tree, static, "task-2")

  assert found == {
    "live": "running",
    "held": True,
    "holder": True,
    "stopped": None,
    "current": {
      "state": "check",
      "visit": 1,
      "index": 1,
      "recorded": False,
      "since": "2026-01-02T03:14:05+00:00",
    },
    "state": None,
    "status": None,
    "reason": None,
    "note": None,
  }


def test_a_visit_with_no_outcome_is_the_one_that_runs(
  tree: Tree, static: Path
) -> None:
  record = two_states()
  visits = cast(list[dict[str, object]], record["visits"])
  visits.append(
    visits[0]
    | {
      "visit": 2,
      "outcome": None,
      "actions": [
        {"started_at": "2026-01-02T03:20:00+00:00", "ended_at": None},
        {"started_at": "2026-01-02T03:15:00+00:00", "ended_at": None},
      ],
    }
  )
  write(tree, "task-2", record, 10)

  with holding(tree.root / "batch-1" / ".lock"):
    found = state_of(tree, static, "task-2")

  assert found["current"] == {
    "state": "write",
    "visit": 2,
    "index": 1,
    "recorded": True,
    "since": "2026-01-02T03:15:00+00:00",
  }


def test_an_attempt_with_no_visit_is_in_its_first_state(
  tree: Tree, static: Path
) -> None:
  none: list[object] = []
  write(tree, "task-2", two_states() | {"visits": none}, 10)

  with holding(tree.root / "batch-1" / ".lock"):
    found = state_of(tree, static, "task-2")

  assert found["current"] == {
    "state": "write",
    "visit": 1,
    "index": 0,
    "recorded": False,
    "since": None,
  }


def test_only_a_running_attempt_has_a_current_visit(
  tree: Tree, static: Path
) -> None:
  write(tree, "task-2", two_states(), 10)
  write(tree, "task-0", two_states(), 20)

  with holding(tree.root / "batch-1" / ".lock"):
    older = state_of(tree, static, "task-0")
    ended = state_of(tree, static, "task-1")
  free = state_of(tree, static, "task-2")

  assert older == {
    "live": "may have stopped",
    "held": True,
    "holder": False,
    "stopped": None,
    "current": None,
    "state": None,
    "status": None,
    "reason": None,
    "note": None,
  }
  assert ended == {
    "live": None,
    "held": True,
    "holder": False,
    "stopped": None,
    "current": None,
    "state": None,
    "status": "passed",
    "reason": None,
    "note": None,
  }
  assert free == {
    "live": "may have stopped",
    "held": False,
    "holder": False,
    "stopped": None,
    "current": None,
    "state": None,
    "status": None,
    "reason": None,
    "note": None,
  }


def test_a_transition_to_the_end_has_no_current_visit(
  tree: Tree, static: Path
) -> None:
  # The fixture's write goes to the end with done: the attempt is ending.
  with holding(tree.root / "batch-1" / ".lock"):
    found = state_of(tree, static, "task-2")

  assert found == {
    "live": "running",
    "held": True,
    "holder": True,
    "stopped": None,
    "current": None,
    "state": None,
    "status": None,
    "reason": None,
    "note": None,
  }


def test_live_refuses_an_attempt_that_is_not_there(
  tree: Tree, static: Path
) -> None:
  web = TestClient(create_app(load_site(tree.site), static))

  missing = web.get(LIVE, params={"attempt": "batch-1/task-9"})
  outside = web.get(LIVE, params={"attempt": "../batch-1"})

  assert missing.status_code == 404
  assert outside.status_code == 404


def test_a_visit_with_no_outcome_nor_action_began_as_the_last_ended(
  tree: Tree, static: Path
) -> None:
  # As a record written when a visit begins, before any action ended.
  record = two_states()
  visits = cast(list[dict[str, object]], record["visits"])
  visits.append({"state": "check", "visit": 1, "outcome": None, "actions": []})
  write(tree, "task-2", record, 10)

  with holding(tree.root / "batch-1" / ".lock"):
    found = state_of(tree, static, "task-2")

  assert found["current"] == {
    "state": "check",
    "visit": 1,
    "index": 1,
    "recorded": True,
    "since": "2026-01-02T03:14:05+00:00",
  }


def holders(tree: Tree, static: Path) -> dict[str, tuple[object, object]]:
  web = TestClient(create_app(load_site(tree.site), static))
  rows = cast(list[dict[str, object]], web.get(ATTEMPTS).json())
  return {str(r["id"]): (r["live"], r["holder"]) for r in rows}


def test_a_held_lock_is_held_for_its_items_attempt_that_wrote_last(
  tree: Tree, static: Path
) -> None:
  with holding(tree.root / "batch-1" / ".lock"):
    found = holders(tree, static)
  free = holders(tree, static)

  assert found == {
    "batch-1/task-2": ("running", True),
    "batch-1/task-0": ("may have stopped", False),
    "batch-1/task-1": (None, False),
  }
  assert {holder for _, holder in free.values()} == {False}


def test_an_attempt_that_ended_since_does_not_take_the_lock_from_one_that_runs(
  tree: Tree, static: Path
) -> None:
  # An older attempt ended, its record written after the one that runs, as
  # a dataset that closes a killed attempt before going on with another.
  write(tree, "task-0", fixture() | {"status": "error"}, 1)

  with holding(tree.root / "batch-1" / ".lock"):
    found = holders(tree, static)

  assert found["batch-1/task-2"] == ("running", True)
  assert found["batch-1/task-0"] == (None, False)


def test_an_attempt_with_no_record_yet_holds_the_lock_once_it_is_the_newest(
  tree: Tree, static: Path
) -> None:
  # An attempt that has just started, its directory made after every other
  # attempt wrote, and an old one that never wrote a record.
  old = tree.root / "batch-1" / "task-old"
  old.mkdir()
  then = time.time() - 100
  os.utime(old, (then, then))
  (tree.root / "batch-1" / "task-3").mkdir()

  with holding(tree.root / "batch-1" / ".lock"):
    found = holders(tree, static)

  assert found["batch-1/task-3"] == (None, True)
  assert found["batch-1/task-old"] == (None, False)
  assert found["batch-1/task-2"] == ("may have stopped", False)


def test_without_a_lock_holder_is_null(tree: Tree, static: Path) -> None:
  _ = tree.project.write_text(PROJECT)

  found = holders(tree, static)

  assert {holder for _, holder in found.values()} == {None}

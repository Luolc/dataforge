# pyright: reportPrivateUsage=false
import json
import os
from pathlib import Path
import shutil
from typing import cast

from fastapi.testclient import TestClient
import pytest

from dataforge.viz import app as viz_app
from dataforge.viz import paths
from dataforge.viz.app import create_app
from dataforge.viz.config import load_site
from tests.viz.sites import FIXTURES, make_tree, Tree

URL = "/api/projects/example/attempts"


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
  return make_tree(tmp_path)


@pytest.fixture
def web(tree: Tree, tmp_path: Path) -> TestClient:
  dist = tmp_path / "dist"
  dist.mkdir()
  _ = (dist / "index.html").write_text("<!doctype html>")
  return TestClient(create_app(load_site(tree.site), dist))


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
  # The records the listing read from a file, in order.
  seen: list[str] = []
  real = paths.read_record

  def counting(*args: object) -> object:
    seen.append("read")
    return real(*args)  # pyright: ignore[reportArgumentType]

  monkeypatch.setattr(viz_app, "read_record", counting, raising=True)
  return seen


def rows(web: TestClient) -> list[dict[str, object]]:
  return cast(list[dict[str, object]], web.get(URL).json())


def test_an_unchanged_record_is_read_once(
  web: TestClient, reads: list[str]
) -> None:
  first = rows(web)
  second = rows(web)

  assert len(reads) == 1
  assert second == first
  assert first[0]["status"] == "passed"


def test_a_rewritten_record_of_the_same_size_is_read_again(
  tree: Tree, web: TestClient, reads: list[str]
) -> None:
  path = tree.attempt / "record.json"
  before = path.stat()
  assert rows(web)[0]["status"] == "passed"

  _ = path.write_bytes(path.read_bytes().replace(b"passed", b"failed"))
  os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
  after = path.stat()

  assert after.st_size == before.st_size
  assert rows(web)[0]["status"] == "failed"
  assert len(reads) == 2


def test_a_replaced_record_with_the_same_size_and_time_is_read_again(
  tree: Tree, web: TestClient, reads: list[str]
) -> None:
  path = tree.attempt / "record.json"
  before = path.stat()
  assert rows(web)[0]["status"] == "passed"

  new = tree.attempt / "new.json"
  _ = new.write_bytes(path.read_bytes().replace(b"passed", b"failed"))
  os.utime(new, ns=(before.st_atime_ns, before.st_mtime_ns))
  os.replace(new, path)
  after = path.stat()

  assert (after.st_size, after.st_mtime_ns) == (
    before.st_size,
    before.st_mtime_ns,
  )
  assert after.st_ino != before.st_ino
  assert rows(web)[0]["status"] == "failed"
  assert len(reads) == 2


def test_an_added_and_a_removed_attempt_change_the_listing(
  tree: Tree, web: TestClient, reads: list[str]
) -> None:
  assert [r["id"] for r in rows(web)] == ["batch-1/task-1"]
  other = tree.root / "batch-1" / "task-2"
  other.mkdir()
  _ = shutil.copy(FIXTURES / "record.json", other / "record.json")

  assert {r["id"] for r in rows(web)} == {"batch-1/task-1", "batch-1/task-2"}
  assert len(reads) == 2

  shutil.rmtree(other)

  assert [r["id"] for r in rows(web)] == ["batch-1/task-1"]
  assert len(reads) == 2
  assert len(viz_app._SUMMARIES) == 1


def test_a_row_changed_by_the_caller_does_not_change_the_next(
  tree: Tree,
) -> None:
  project = load_site(tree.site).projects[0]
  first = viz_app._row(project, "batch-1/task-1")[0]
  cast(list[object], first["rounds"]).clear()
  cast(list[object], first["values"]).clear()

  second = viz_app._row(project, "batch-1/task-1")[0]

  assert second["rounds"] != []
  assert second["values"] != []


def test_an_unreadable_record_shows_its_error_every_time(
  tree: Tree, web: TestClient
) -> None:
  _ = (tree.attempt / "record.json").write_text("{not json")

  first = rows(web)
  second = rows(web)

  assert first[0]["error"] == "the record is not JSON"
  assert second == first


def test_a_record_that_is_not_an_object_shows_its_error(
  tree: Tree, web: TestClient
) -> None:
  _ = (tree.attempt / "record.json").write_text(json.dumps([1]))

  assert rows(web)[0]["error"] == "the record is not a JSON object"

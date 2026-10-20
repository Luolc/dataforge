from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
import pytest

from dataforge.viz.app import create_app
from dataforge.viz.config import load_site
from tests.viz.sites import make_tree, Tree

API = "/api/projects/example"


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
  return make_tree(tmp_path)


def client(tree: Tree) -> TestClient:
  return TestClient(create_app(load_site(tree.site), tree.root / "no-static"))


@pytest.mark.parametrize(
  "path",
  [
    "../../../outside.txt",
    "/etc/hostname",
    "artifacts//write",
    "artifacts/./write",
    "artifacts/write/..",
    "artifacts/../record.json",
  ],
)
def test_a_path_out_of_the_root_is_refused(tree: Tree, path: str) -> None:
  response = client(tree).get(
    f"{API}/files", params={"attempt": "batch-1/task-1", "path": path}
  )

  assert response.status_code == 404
  assert response.json()["reason"]


@pytest.mark.parametrize(
  "attempt",
  [
    "..",
    "../..",
    "batch-1/..",
    "/batch-1/task-1",
    "batch-1",
    "batch-1/task-1/",
  ],
)
def test_an_attempt_out_of_the_pattern_is_refused(
  tree: Tree, attempt: str
) -> None:
  web = client(tree)

  for route in ("record", "files"):
    response = web.get(f"{API}/{route}", params={"attempt": attempt})
    assert response.status_code == 404, route
    assert "is not an attempt" in response.json()["reason"]


def test_a_symlink_is_listed_and_never_opened(tree: Tree) -> None:
  outside = tree.root.parent.parent / "outside.txt"
  (tree.attempt / "leak.txt").symlink_to(outside)
  (tree.attempt / "up").symlink_to(tree.root.parent.parent)
  web = client(tree)
  query = {"attempt": "batch-1/task-1"}

  listing = cast(
    dict[str, list[object]], web.get(f"{API}/files", params=query).json()
  )
  for path in ("leak.txt", "up", "up/outside.txt"):
    response = web.get(f"{API}/files", params=query | {"path": path})
    assert response.status_code == 404, path
    assert "a host file" not in response.text

  assert {"name": "leak.txt", "type": "symlink", "size": None} in listing[
    "entries"
  ]
  assert {"name": "up", "type": "symlink", "size": None} in listing["entries"]


def test_a_symlinked_record_is_never_read(tree: Tree) -> None:
  record = tree.attempt / "record.json"
  real = tree.root.parent.parent / "real-record.json"
  _ = record.rename(real)
  record.symlink_to(real)
  web = client(tree)

  response = web.get(f"{API}/record", params={"attempt": "batch-1/task-1"})
  [row] = cast(list[dict[str, object]], web.get(f"{API}/attempts").json())

  assert response.status_code == 404
  assert row["values"] is None
  assert "symlink" in str(row["error"])


def test_a_symlinked_attempt_is_not_listed_or_opened(tree: Tree) -> None:
  (tree.root / "batch-1" / "task-2").symlink_to(tree.attempt)
  web = client(tree)

  rows = cast(list[dict[str, object]], web.get(f"{API}/attempts").json())
  ids = [row["id"] for row in rows]
  response = web.get(f"{API}/record", params={"attempt": "batch-1/task-2"})

  assert ids == ["batch-1/task-1"]
  assert response.status_code == 404


def test_a_directory_lists_its_entries_by_name(tree: Tree) -> None:
  _ = (tree.attempt / "a.txt").write_bytes(b"12345")
  record = (tree.attempt / "record.json").stat().st_size

  response = client(tree).get(
    f"{API}/files", params={"attempt": "batch-1/task-1"}
  )

  assert response.json() == {
    "entries": [
      {"name": "a.txt", "type": "file", "size": 5},
      {"name": "artifacts", "type": "dir", "size": None},
      {"name": "record.json", "type": "file", "size": record},
    ]
  }


def test_a_file_over_the_limit_is_refused(
  tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setattr("dataforge.viz.app.FILE_LIMIT", 4)
  _ = (tree.attempt / "four.txt").write_bytes(b"1234")
  _ = (tree.attempt / "five.txt").write_bytes(b"12345")
  web = client(tree)
  query = {"attempt": "batch-1/task-1"}

  four = web.get(f"{API}/files", params=query | {"path": "four.txt"})
  five = web.get(f"{API}/files", params=query | {"path": "five.txt"})

  assert (four.status_code, four.content) == (200, b"1234")
  assert five.status_code == 413
  assert "5 bytes" in five.json()["reason"]

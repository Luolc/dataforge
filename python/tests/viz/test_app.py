import os
from pathlib import Path
from typing import cast

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest

from dataforge.viz.app import create_app, CSP
from dataforge.viz.config import load_site
from tests.viz.sites import FIXTURES, make_tree, Tree

API = "/api/projects/example"
QUERY = {"attempt": "batch-1/task-1"}


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
  return make_tree(tmp_path)


@pytest.fixture
def static(tmp_path: Path) -> Path:
  dist = tmp_path / "dist"
  (dist / "assets").mkdir(parents=True)
  _ = (dist / "index.html").write_text("<!doctype html><title>viz</title>")
  _ = (dist / "assets" / "app.js").write_text("console.log(1)")
  return dist


def client(tree: Tree, static: Path) -> TestClient:
  return TestClient(create_app(load_site(tree.site), static))


def snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
  found: dict[str, tuple[int, int, int]] = {}
  for top, dirs, files in os.walk(root):
    for name in dirs + files:
      path = Path(top) / name
      status = path.lstat()
      found[str(path)] = (status.st_mode, status.st_size, status.st_mtime_ns)
  return found


def test_every_route_is_a_get_and_writes_nothing(
  tree: Tree, static: Path
) -> None:
  app = create_app(load_site(tree.site), static)
  web = TestClient(app)
  before = snapshot(tree.root.parent.parent)
  urls = [
    "/api/projects",
    f"{API}/attempts",
    f"{API}/record?attempt=batch-1/task-1",
    f"{API}/files?attempt=batch-1/task-1&path=artifacts",
    "/index.html",
  ]

  methods = {
    m for r in app.routes if isinstance(r, APIRoute) for m in r.methods or ()
  }
  for url in urls:
    assert web.get(url).status_code == 200, url
    for method in ("POST", "PUT", "PATCH", "DELETE"):
      assert web.request(method, url).status_code == 405, (method, url)

  assert methods == {"GET"}
  assert snapshot(tree.root.parent.parent) == before


def test_the_record_is_the_file_as_written(tree: Tree, static: Path) -> None:
  response = client(tree, static).get(f"{API}/record", params=QUERY)

  assert response.status_code == 200
  assert response.headers["content-type"] == "application/json"
  assert response.content == (FIXTURES / "record.json").read_bytes()


def test_files_are_served_as_bytes_with_nosniff(
  tree: Tree, static: Path
) -> None:
  _ = (tree.attempt / "page.html").write_text("<script>alert(1)</script>")

  response = client(tree, static).get(
    f"{API}/files", params=QUERY | {"path": "page.html"}
  )

  assert response.status_code == 200
  assert response.headers["content-type"] == "application/octet-stream"
  assert response.headers["x-content-type-options"] == "nosniff"
  assert response.content == b"<script>alert(1)</script>"


def test_every_response_has_the_csp(tree: Tree, static: Path) -> None:
  web = client(tree, static)
  urls = [
    "/api/projects",
    f"{API}/record?attempt=batch-1/task-1",
    f"{API}/record",
    f"{API}/record?attempt=..",
    "/api/nothing",
    "/",
    "/assets/app.js",
  ]

  for url in urls:
    response = web.get(url)
    assert response.headers["content-security-policy"] == CSP, url
    assert response.headers["x-content-type-options"] == "nosniff", url


def test_projects_leave_out_where_the_files_are(
  tree: Tree, static: Path
) -> None:
  [project] = cast(
    list[object], client(tree, static).get("/api/projects").json()
  )

  assert project == {
    "id": "example",
    "name": "Example",
    "color": "#e5a54b",
    "states": {},
    "statuses": {"passed": {"label": "Passed", "tone": "good"}},
    "columns": [
      {"title": "Status", "field": ["status"], "format": "text"},
      {"title": "Cost", "field": ["budget", "cost_usd"], "format": "usd"},
      {"title": "Missing", "field": ["absent", "key"], "format": "text"},
    ],
    "session": {"thinking": "collapsed"},
  }


def test_attempts_are_newest_first_with_their_values(
  tree: Tree, static: Path
) -> None:
  newer = tree.root / "batch-2" / "task-9"
  newer.mkdir(parents=True)
  _ = (newer / "record.json").write_text('{"status": "too_easy"}')
  os.utime(tree.attempt / "record.json", (1_000_000_000, 1_000_000_000))
  (tree.root / "batch-2" / "task-8").mkdir()
  bad = tree.root / "batch-2" / "task-7"
  bad.mkdir()
  _ = (bad / "record.json").write_text("{not json")
  os.utime(bad / "record.json", (1_500_000_000, 1_500_000_000))

  rows = cast(
    list[dict[str, object]],
    client(tree, static).get(f"{API}/attempts").json(),
  )

  assert [r["id"] for r in rows] == [
    "batch-2/task-9",
    "batch-2/task-7",
    "batch-1/task-1",
    "batch-2/task-8",
  ]
  assert rows[0]["values"] == ["too_easy", None, None]
  assert rows[1]["reason"] == "the record is not JSON"
  assert rows[2]["values"] == ["passed", 0.42, None]
  assert rows[2]["modified"] == "2001-09-09T01:46:40+00:00"
  assert rows[3]["modified"] is None
  assert "No such file" in str(rows[3]["reason"])


@pytest.mark.parametrize(
  ("url", "status"),
  [
    (f"{API}/record", 400),
    ("/api/projects/other/attempts", 404),
    ("/api/nothing", 404),
  ],
)
def test_an_api_error_is_json_with_a_reason(
  tree: Tree, static: Path, url: str, status: int
) -> None:
  response = client(tree, static).get(url)

  assert response.status_code == status
  assert set(cast(dict[str, object], response.json())) == {"error", "reason"}


def test_other_paths_serve_the_frontend(tree: Tree, static: Path) -> None:
  web = client(tree, static)

  script = web.get("/assets/app.js")
  page = web.get("/example/attempt", params=QUERY)
  outside = web.get("/assets/../../site.yaml")

  assert script.text == "console.log(1)"
  assert "javascript" in script.headers["content-type"]
  assert page.text.startswith("<!doctype html>")
  assert outside.text.startswith("<!doctype html>")

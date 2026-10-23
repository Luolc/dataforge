import json
import os
from pathlib import Path
from typing import cast

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest

from dataforge.viz.app import create_app
from dataforge.viz.config import load_site
from tests.viz.sites import FIXTURES, make_tree, PROJECT, Tree

API = "/api/projects/example"
QUERY = {"attempt": "batch-1/task-1"}
# The only routes that write, and only to the job queue (test_actions.py).
WRITES = ("/api/projects/{p}/actions/{name}", "/api/queue/jobs/{job}/cancel")


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
    f"{API}/live?attempt=batch-1/task-1",
    f"{API}/files?attempt=batch-1/task-1&path=artifacts",
    f"{API}/tabs?attempt=batch-1/task-1",
    "/index.html",
  ]

  routes = [r for r in app.routes if isinstance(r, APIRoute)]
  writes = {r.path: r.methods for r in routes if r.path in WRITES}
  methods = {m for r in routes if r.path not in WRITES for m in r.methods or ()}
  for url in urls:
    assert web.get(url).status_code == 200, url
    assert web.head(url).status_code == 200, url
    for method in ("POST", "PUT", "PATCH", "DELETE"):
      assert web.request(method, url).status_code == 405, (method, url)

  assert methods == {"GET", "HEAD"}
  assert writes == {path: {"POST"} for path in WRITES}
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


# Invariant 3: scripts, images and connections only from the viewer's own
# origin, images also as data:.
CSP = (
  "default-src 'self'; script-src 'self'; connect-src 'self';"
  " img-src 'self' data:; font-src 'self'; style-src 'self';"
  " object-src 'none'; base-uri 'none'; frame-ancestors 'none';"
  " form-action 'none'"
)


def test_every_response_has_the_csp(tree: Tree, static: Path) -> None:
  web = client(tree, static)
  urls = [
    "/api/projects",
    f"{API}/record?attempt=batch-1/task-1",
    f"{API}/live?attempt=batch-1/task-1",
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
    "item": {"depth": 1, "title": "Batch"},
    "columns": [
      {"title": "Status", "field": ["status"], "format": "text"},
      {"title": "Cost", "field": ["budget", "cost_usd"], "format": "usd"},
      {"title": "Missing", "field": ["absent", "key"], "format": "text"},
    ],
    "session": {"thinking": "hidden"},
    "not_ended": None,
    "runs": None,
    "deliverables": [],
    "kinds": {},
    "trials": None,
    "definition": None,
    "task": None,
    "monitor": False,
    "actions": [],
    "jobs": [],
    "tabs": [],
  }


def test_projects_carry_not_ended(tree: Tree, static: Path) -> None:
  _ = tree.project.write_text(PROJECT + "not_ended: [running, queued]\n")

  [project] = cast(
    list[dict[str, object]], client(tree, static).get("/api/projects").json()
  )

  assert project["not_ended"] == ["running", "queued"]


def test_projects_carry_their_deliverables(tree: Tree, static: Path) -> None:
  _ = tree.project.write_text(
    PROJECT
    + "deliverables:\n"
    + "  - {name: task, kind: files, title: Task}\n"
    + "  - {name: review, kind: report, title: Review}\n"
    + "  - {name: regrade, kind: trials, title: Regrade, no_trace: No agent.}\n"
  )

  [project] = cast(
    list[dict[str, object]], client(tree, static).get("/api/projects").json()
  )

  assert project["deliverables"] == [
    {
      "name": "task",
      "kind": "files",
      "title": "Task",
      "no_trace": None,
      "default": False,
    },
    {
      "name": "review",
      "kind": "report",
      "title": "Review",
      "no_trace": None,
      "default": False,
    },
    {
      "name": "regrade",
      "kind": "trials",
      "title": "Regrade",
      "no_trace": "No agent.",
      "default": False,
    },
  ]


def project_deliverables(project: dict[str, object]) -> list[dict[str, object]]:
  return cast(list[dict[str, object]], project["deliverables"])


def test_projects_carry_a_kind_s_title_and_its_default(
  tree: Tree, static: Path
) -> None:
  _ = tree.project.write_text(
    PROJECT
    + "deliverables:\n"
    + "  - {name: regrade, kind: trials, title: Regrade}\n"
    + "  - {name: pass_at_k, kind: trials, title: Pass@k, default: true}\n"
    + "  - {name: review, kind: report, title: Review}\n"
    + "kinds: {report: {title: Reviews}}\n"
  )

  [project] = cast(
    list[dict[str, object]], client(tree, static).get("/api/projects").json()
  )

  assert [(d["name"], d["default"]) for d in project_deliverables(project)] == [
    ("regrade", False),
    ("pass_at_k", True),
    ("review", False),
  ]
  assert project["kinds"] == {"report": {"title": "Reviews"}}


def test_projects_carry_the_trial_diff(tree: Tree, static: Path) -> None:
  _ = tree.project.write_text(PROJECT + "trials: {diff: verifier/patch.diff}\n")

  [project] = cast(
    list[dict[str, object]], client(tree, static).get("/api/projects").json()
  )

  assert project["trials"] == {
    "diff": "verifier/patch.diff",
    "tests": None,
    "kinds": {},
  }


def test_projects_carry_the_trial_tests_and_their_kinds(
  tree: Tree, static: Path
) -> None:
  _ = tree.project.write_text(
    PROJECT
    + "trials:\n"
    + "  tests: verifier/ctrf.json\n"
    + "  kinds: {security: {label: Security}}\n"
  )

  [project] = cast(
    list[dict[str, object]], client(tree, static).get("/api/projects").json()
  )

  assert project["trials"] == {
    "diff": None,
    "tests": "verifier/ctrf.json",
    "kinds": {"security": {"label": "Security"}},
  }


def test_projects_carry_the_definition_s_path(tree: Tree, static: Path) -> None:
  _ = tree.project.write_text(PROJECT + "definition: run/definition.json\n")

  [project] = cast(
    list[dict[str, object]], client(tree, static).get("/api/projects").json()
  )

  assert project["definition"] == "run/definition.json"


def test_projects_carry_the_task_s_path(tree: Tree, static: Path) -> None:
  _ = tree.project.write_text(PROJECT + "task: task/task.toml\n")

  [project] = cast(
    list[dict[str, object]], client(tree, static).get("/api/projects").json()
  )

  assert project["task"] == "task/task.toml"


def test_attempts_are_newest_first_with_their_values(
  tree: Tree, static: Path
) -> None:
  newer = tree.root / "batch-2" / "task-9"
  newer.mkdir(parents=True)
  _ = (newer / "record.json").write_text('{"status": 3, "visits": "no"}')
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
  assert rows[0] | {"modified": None} == {
    "id": "batch-2/task-9",
    "item": "batch-2",
    "modified": None,
    "status": None,
    "reason": None,
    "cost_usd": None,
    "started_at": None,
    "ended_at": None,
    "rounds": [],
    "values": [3, None, None],
    "reads": [None, None, None],
    "queued": [None, None, None],
    "live": "not ended",
    "held": None,
    "state": None,
    "note": None,
    "progress_at": None,
    "error": None,
  }
  assert rows[1]["error"] == "the record is not JSON"
  assert rows[2] == {
    "id": "batch-1/task-1",
    "item": "batch-1",
    "modified": "2001-09-09T01:46:40+00:00",
    "status": "passed",
    "reason": None,
    "cost_usd": 0.42,
    "started_at": "2026-01-02T03:04:05+00:00",
    "ended_at": "2026-01-02T03:14:05+00:00",
    "rounds": [{"round": 1, "state": "write", "outcome": "done"}],
    "values": ["passed", 0.42, None],
    "reads": [None, None, None],
    "queued": [None, None, None],
    "live": None,
    "held": None,
    "state": None,
    "note": None,
    "progress_at": None,
    "error": None,
  }
  assert rows[3]["modified"] is None
  assert "No such file" in str(rows[3]["error"])


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


def test_rounds_are_the_last_visit_of_each_round(
  tree: Tree, static: Path
) -> None:
  visits = [
    {"state": "write", "round": 1, "outcome": "done"},
    {"state": "check", "round": 1, "outcome": "fail"},
    {"state": "write", "round": 2, "outcome": "done"},
    {"state": "check", "round": 2, "outcome": "pass"},
    {"state": "solve", "round": 2, "outcome": None},
  ]
  _ = (tree.attempt / "record.json").write_text(
    json.dumps({"status": None, "visits": visits})
  )

  [row] = cast(
    list[dict[str, object]],
    client(tree, static).get(f"{API}/attempts").json(),
  )

  assert row["rounds"] == [
    {"round": 1, "state": "check", "outcome": "fail"},
    {"round": 2, "state": "solve", "outcome": None},
  ]

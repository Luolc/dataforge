from collections.abc import Generator
from contextlib import closing, contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import cast

from fastapi.testclient import TestClient
import pytest

from dataforge import queue
from dataforge.viz import app
from dataforge.viz.app import create_app
from dataforge.viz.config import load_site
from tests.viz.sites import FIXTURES, make_queue_tree, QueueTree

ORIGIN = "http://127.0.0.1:8800"
WRITE = {"X-Viz-Write": "1", "Origin": ORIGIN}
ACTIONS = "/api/projects/example/actions"


@pytest.fixture
def made(tmp_path: Path) -> QueueTree:
  made = make_queue_tree(tmp_path)
  _ = (made.tree.root / "batch-1" / ".lock").write_text("")
  return made


@pytest.fixture
def static(tmp_path: Path) -> Path:
  dist = tmp_path / "dist"
  dist.mkdir()
  _ = (dist / "index.html").write_text("<!doctype html>")
  return dist


def client(made: QueueTree, static: Path) -> TestClient:
  # The address the site file binds, so that Host is the viewer's own.
  return TestClient(
    create_app(load_site(made.tree.site), static), base_url=ORIGIN
  )


def config(made: QueueTree) -> queue.Config:
  return queue.load_config(made.queue / "queue.yaml")


def jobs(made: QueueTree) -> list[queue.Job]:
  try:
    return queue.jobs(config(made))
  except FileNotFoundError:
    return []


def write(made: QueueTree, name: str, status: str | None, age: int) -> None:
  record = cast(
    dict[str, object], json.loads((FIXTURES / "record.json").read_text())
  )
  path = made.tree.root / "batch-1" / name / "record.json"
  path.parent.mkdir(exist_ok=True)
  _ = path.write_text(json.dumps(record | {"status": status}))
  then = time.time() - age
  os.utime(path, (then, then))


@contextmanager
def holding(path: Path) -> Generator[None]:
  with path.open("a") as f:
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield


def reasons(web: TestClient) -> dict[str, str | None]:
  response = web.get(ACTIONS, params={"item": "batch-1"})
  assert response.status_code == 200
  rows = cast(list[dict[str, str | None]], response.json())
  return {str(r["name"]): r["reason"] for r in rows}


def test_an_action_queues_its_command_with_the_item_alone(
  made: QueueTree, static: Path
) -> None:
  response = client(made, static).post(
    f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE
  )

  assert response.status_code == 201
  [job] = jobs(made)
  assert job.queue == "fast"
  assert job.argv == ("run-item", "--item", "batch-1", "--note=from batch-1")
  assert (job.cwd, job.memory_mib) == (str(made.work), 2048)
  assert job.dedup_key == "example:batch-1"
  assert job.labels == {
    "project": "example",
    "action": "again",
    "item": "batch-1",
  }
  shown = cast(dict[str, dict[str, object]], response.json())["job"]
  assert shown["id"] == job.id
  assert shown["state"] == "queued"
  assert not {"argv", "cwd", "log", "pid", "dedup_key"} & set(shown)


@pytest.mark.parametrize(
  "headers",
  [
    {"Origin": ORIGIN},
    {"X-Viz-Write": "1"},
    {"X-Viz-Write": "0", "Origin": ORIGIN},
    {"X-Viz-Write": "1", "Origin": "http://evil.example"},
    {"X-Viz-Write": "1", "Origin": "null"},
    {"X-Viz-Write": "1", "Origin": "https://127.0.0.1:8800"},
    # DNS rebinding: a name of the attacker's resolves to the viewer's
    # address, and the browser sends that name as Host and in Origin.
    {
      "X-Viz-Write": "1",
      "Origin": "http://evil.example:8800",
      "Host": "evil.example:8800",
    },
    {"X-Viz-Write": "1", "Origin": ORIGIN, "Host": "evil.example:8800"},
    WRITE | {"Sec-Fetch-Site": "cross-site"},
    WRITE | {"Sec-Fetch-Site": "same-site"},
  ],
)
def test_a_write_from_anywhere_but_the_viewer_s_page_is_refused(
  made: QueueTree, static: Path, headers: dict[str, str]
) -> None:
  web = client(made, static)

  submitted = web.post(
    f"{ACTIONS}/again", params={"item": "batch-1"}, headers=headers
  )

  assert submitted.status_code == 403
  assert jobs(made) == []
  # A control: the same write with the viewer's own headers goes through.
  assert (
    web.post(
      f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE
    ).status_code
    == 201
  )
  [job] = jobs(made)
  canceled = web.post(f"/api/queue/jobs/{job.id}/cancel", headers=headers)
  assert canceled.status_code == 403
  assert [j.state for j in jobs(made)] == ["queued"]


@pytest.mark.parametrize(
  "params",
  [
    {"item": "batch-1", "argv": "sh"},
    {"item": "batch-1", "queue": "slow"},
    {"item": "batch-1", "cwd": "/"},
    [("item", "batch-1"), ("item", "batch-2")],
    {},
  ],
)
def test_a_write_takes_nothing_but_the_item(
  made: QueueTree, static: Path, params: object
) -> None:
  response = client(made, static).post(
    f"{ACTIONS}/again",
    params=cast(dict[str, str], params),
    headers=WRITE,
  )

  assert response.status_code == 400
  assert jobs(made) == []


def test_a_write_takes_no_body(made: QueueTree, static: Path) -> None:
  response = client(made, static).post(
    f"{ACTIONS}/again",
    params={"item": "batch-1"},
    headers=WRITE,
    content=b'{"argv": ["sh"]}',
  )

  assert response.status_code == 400
  assert jobs(made) == []


@pytest.mark.parametrize(
  ("item", "status"),
  [
    ("batch-x", 400),
    ("batch-1/task-1", 400),
    ("../batch-1", 400),
    ("-batch-1", 400),
    ("batch-1\n", 400),
    ("batch-" + "1" * 300, 400),
    ("batch-2", 404),
  ],
)
def test_an_item_that_is_not_one_of_the_project_s_is_refused(
  made: QueueTree, static: Path, item: str, status: int
) -> None:
  web = client(made, static)

  submitted = web.post(f"{ACTIONS}/again", params={"item": item}, headers=WRITE)

  assert submitted.status_code == status
  assert web.get(ACTIONS, params={"item": item}).status_code == status
  assert jobs(made) == []


def test_an_action_the_project_does_not_name_is_refused(
  made: QueueTree, static: Path
) -> None:
  response = client(made, static).post(
    f"{ACTIONS}/sh", params={"item": "batch-1"}, headers=WRITE
  )

  assert response.status_code == 404
  assert jobs(made) == []


def test_an_item_with_a_job_of_its_key_queues_no_other(
  made: QueueTree, static: Path
) -> None:
  web = client(made, static)
  write(made, "task-2", None, 0)
  first = web.post(
    f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE
  )
  job = cast(dict[str, dict[str, int]], first.json())["job"]["id"]

  again = web.post(
    f"{ACTIONS}/go-on", params={"item": "batch-1"}, headers=WRITE
  )

  assert again.status_code == 409
  assert reasons(web) == {
    "again": f"Job {job} for this item is queued in fast.",
    "go-on": f"Job {job} for this item is queued in fast.",
  }
  assert len(jobs(made)) == 1


def test_a_job_another_way_queued_also_holds_the_key(
  made: QueueTree, static: Path
) -> None:
  web = client(made, static)
  write(made, "task-2", None, 0)
  _ = queue.submit(
    config(made),
    "slow",
    ["elsewhere"],
    cwd=made.work,
    memory_mib=1,
    key="example:batch-1",
    labels={},
  )

  response = web.post(
    f"{ACTIONS}/go-on", params={"item": "batch-1"}, headers=WRITE
  )

  assert response.status_code == 409
  assert len(jobs(made)) == 1


def test_idle_waits_for_the_item_s_lock(made: QueueTree, static: Path) -> None:
  web = client(made, static)
  lock = made.tree.root / "batch-1" / ".lock"

  with holding(lock):
    held = reasons(web)["again"]
    refused = web.post(
      f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE
    )

  assert held == "An attempt of this item is running: its lock is held."
  assert refused.status_code == 409
  assert jobs(made) == []
  assert reasons(web)["again"] is None


def test_stopped_needs_a_newest_attempt_that_may_have_stopped(
  made: QueueTree, static: Path
) -> None:
  web = client(made, static)
  lock = made.tree.root / "batch-1" / ".lock"
  # The fixture's attempt has ended.
  ended = reasons(web)["go-on"]
  # An older one that has not ended does not count: the newest has ended.
  write(made, "task-0", None, 3600)
  older = reasons(web)["go-on"]
  write(made, "task-2", None, 0)
  with holding(lock):
    running = reasons(web)["go-on"]
  stopped = reasons(web)["go-on"]

  assert ended == "The newest attempt of this item has ended."
  assert older == ended
  assert running == "The newest attempt of this item is running."
  assert stopped is None
  response = web.post(
    f"{ACTIONS}/go-on", params={"item": "batch-1"}, headers=WRITE
  )
  assert response.status_code == 201
  assert [j.argv for j in jobs(made)] == [("run-item", "--go-on", "batch-1")]


def test_a_queued_job_is_canceled_by_its_id_alone(
  made: QueueTree, static: Path
) -> None:
  web = client(made, static)
  _ = web.post(f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE)
  [job] = jobs(made)

  extra = web.post(
    f"/api/queue/jobs/{job.id}/cancel", params={"signal": "9"}, headers=WRITE
  )
  canceled = web.post(f"/api/queue/jobs/{job.id}/cancel", headers=WRITE)
  missing = web.post(f"/api/queue/jobs/{job.id + 1}/cancel", headers=WRITE)
  bad = web.post("/api/queue/jobs/x/cancel", headers=WRITE)

  assert extra.status_code == 400
  assert canceled.status_code == 200
  assert cast(dict[str, dict[str, str]], canceled.json())["job"]["state"] == (
    "canceled"
  )
  assert [j.state for j in jobs(made)] == ["canceled"]
  assert (missing.status_code, bad.status_code) == (404, 400)


def test_the_queue_lists_its_queues_and_jobs(
  made: QueueTree, static: Path
) -> None:
  web = client(made, static)
  before = web.get("/api/queue")
  _ = web.post(f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE)

  after = web.get("/api/queue")

  assert before.status_code == 200
  assert cast(dict[str, object], before.json())["jobs"] == []
  body = cast(dict[str, object], after.json())
  assert body["queues"] == [
    {"name": "fast", "limit": 2, "running": 0, "queued": 1},
    {"name": "slow", "limit": 1, "running": 0, "queued": 0},
  ]
  assert body["max_running"] == 3
  [job] = cast(list[dict[str, object]], body["jobs"])
  assert job["labels"] == {
    "project": "example",
    "action": "again",
    "item": "batch-1",
  }
  assert not {"argv", "cwd", "log", "pid", "dedup_key"} & set(job)


def test_a_site_with_no_queue_has_no_queue_routes(
  made: QueueTree, static: Path
) -> None:
  text = made.tree.site.read_text()
  _ = made.tree.site.write_text(text.split("queue:")[0])
  _ = made.tree.project.write_text(
    made.tree.project.read_text().split("actions:")[0]
  )
  web = client(made, static)

  assert web.get("/api/queue").status_code == 404
  assert web.get(ACTIONS, params={"item": "batch-1"}).json() == []
  assert web.post("/api/queue/jobs/1/cancel", headers=WRITE).status_code == 404
  assert (
    web.post(
      f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE
    ).status_code
    == 404
  )


def test_the_queue_s_reads_write_nothing(made: QueueTree, static: Path) -> None:
  web = client(made, static)
  write(made, "task-2", None, 0)
  _ = web.post(f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE)
  before = snapshot(made.queue.parent)
  urls = ["/api/queue", f"{ACTIONS}?item=batch-1"]

  for url in urls:
    assert web.get(url).status_code == 200, url
    assert web.head(url).status_code == 200, url
    for method in ("POST", "PUT", "PATCH", "DELETE"):
      response = web.request(method, url, headers=WRITE)
      assert response.status_code == 405, (method, url)

  assert snapshot(made.queue.parent) == before
  assert [j.state for j in jobs(made)] == ["queued"]


def snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
  # SQLite's own -wal and -shm, which a reader of a WAL database may make and
  # write (invariant 1), are left out, and so is a directory's mtime, which
  # making them changes.
  found: dict[str, tuple[int, int, int]] = {}
  for top, dirs, files in os.walk(root):
    for name in dirs + files:
      if name.endswith(("-wal", "-shm")):
        continue
      path = Path(top) / name
      status = path.lstat()
      found[str(path)] = (
        (status.st_mode, 0, 0)
        if name in dirs
        else (status.st_mode, status.st_size, status.st_mtime_ns)
      )
  return found


def test_a_write_s_preflight_is_never_allowed(
  made: QueueTree, static: Path
) -> None:
  # A page of another origin must ask before it sends X-Viz-Write; the
  # viewer answers no such question.
  web = client(made, static)

  for url in (f"{ACTIONS}/again?item=batch-1", "/api/queue/jobs/1/cancel"):
    response = web.options(
      url,
      headers={
        "Origin": "http://evil.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-viz-write",
      },
    )
    assert response.status_code == 405, url
    names: list[str] = list(response.headers.keys())
    assert not [h for h in names if h.startswith("access-control")]


@pytest.mark.parametrize("item", ["-batch-1", "--help", ".batch-1"])
def test_an_item_that_reads_as_an_option_is_refused_whatever_the_pattern(
  made: QueueTree, static: Path, item: str
) -> None:
  text = made.tree.project.read_text()
  _ = made.tree.project.write_text(
    text.replace('pattern: "batch-[0-9]+"', 'pattern: ".+"')
  )
  web = client(made, static)

  submitted = web.post(f"{ACTIONS}/again", params={"item": item}, headers=WRITE)

  assert submitted.status_code == 400
  assert jobs(made) == []


def test_the_queue_lists_the_jobs_that_ended_last_and_counts_them_all(
  made: QueueTree, static: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setattr(app, "ENDED_LIMIT", 2)
  web = client(made, static)
  ids = [
    queue.submit(
      config(made),
      "slow",
      ["x"],
      cwd=made.work,
      memory_mib=1,
      key=None,
      labels={},
    ).id
    for _ in range(3)
  ]
  first, second, third = ids
  # Ended in another order than queued: the third first, then the first.
  for job in (third, first, second):
    _ = queue.cancel(config(made), job)

  body = cast(dict[str, object], web.get("/api/queue").json())

  assert body["ended"] == 3
  assert body["ended_limit"] == 2
  listed = cast(list[dict[str, object]], body["jobs"])
  assert [j["id"] for j in listed] == [second, first]


def test_a_job_the_queue_refuses_shows_no_path(
  made: QueueTree, static: Path
) -> None:
  web = client(made, static)
  made.work.rmdir()

  response = web.post(
    f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE
  )

  assert response.status_code == 503
  assert str(made.work) not in response.text
  assert jobs(made) == []


def test_at_http_s_own_port_a_write_names_no_port(
  made: QueueTree, static: Path
) -> None:
  text = made.tree.site.read_text()
  _ = made.tree.site.write_text(text.replace("port: 8800", "port: 80"))
  web = TestClient(
    create_app(load_site(made.tree.site), static),
    base_url="http://127.0.0.1",
  )

  refused = web.post(
    f"{ACTIONS}/again",
    params={"item": "batch-1"},
    headers={"X-Viz-Write": "1", "Origin": "http://127.0.0.1:80"},
  )
  taken = web.post(
    f"{ACTIONS}/again",
    params={"item": "batch-1"},
    headers={"X-Viz-Write": "1", "Origin": "http://127.0.0.1"},
  )

  assert (refused.status_code, taken.status_code) == (403, 201)


def test_a_running_attempt_with_no_action_yet_starts_with_its_job(
  made: QueueTree, static: Path
) -> None:
  # The record holds a visit with no action: no start of its own to show.
  path = made.tree.root / "batch-1" / "task-2" / "record.json"
  path.parent.mkdir(exist_ok=True)
  _ = path.write_text(
    json.dumps(
      {
        "status": None,
        "visits": [
          {
            "state": "write",
            "visit": 1,
            "round": 1,
            "outcome": None,
            "actions": [],
          }
        ],
        "graph": {"start": "write", "states": [], "edges": []},
      }
    )
  )
  web = client(made, static)
  _ = web.post(f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE)
  [job] = jobs(made)
  with closing(
    sqlite3.connect(made.queue / "queue.sqlite", autocommit=True)
  ) as conn:
    _ = conn.execute(
      "UPDATE jobs SET state = 'running', started_at = 1772442000 WHERE id = ?",
      (job.id,),
    )
  expected = "2026-03-02T09:00:00+00:00"

  with holding(made.tree.root / "batch-1" / ".lock"):
    rows = cast(
      list[dict[str, object]],
      web.get("/api/projects/example/attempts").json(),
    )
    live = cast(
      dict[str, dict[str, object]],
      web.get(
        "/api/projects/example/live", params={"attempt": "batch-1/task-2"}
      ).json(),
    )

  running = next(r for r in rows if r["id"] == "batch-1/task-2")
  assert (running["live"], running["started_at"]) == ("running", expected)
  assert live["current"]["since"] == expected

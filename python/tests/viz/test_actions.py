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
from dataforge.viz import actions, app
from dataforge.viz.app import create_app
from dataforge.viz.config import ConfigError, load_site
from tests.viz.sites import FIXTURES, make_queue_tree, QueueTree, with_params

ORIGIN = "http://127.0.0.1:8800"
WRITE = {"X-Viz-Write": "1", "Origin": ORIGIN}
ACTIONS = "/api/projects/example/actions"
TABS = "/api/projects/example/tabs"


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


def write(
  made: QueueTree,
  name: str,
  status: str | None,
  age: int,
  started: str | None = None,
) -> None:
  record = cast(
    dict[str, object], json.loads((FIXTURES / "record.json").read_text())
  )
  if started is not None:
    visits = cast(list[dict[str, list[dict[str, object]]]], record["visits"])
    visits[0]["actions"][0]["started_at"] = started
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
  body = cast(dict[str, list[dict[str, str | None]]], response.json())
  return {str(r["name"]): r["reason"] for r in body["actions"]}


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

  assert ended == (
    "Go on needs an attempt that may have stopped; the newest, task-1,"
    + " ended Passed."
  )
  assert older == ended
  assert running == "An attempt of this item is running: its lock is held."
  assert stopped is None
  response = web.post(
    f"{ACTIONS}/go-on", params={"item": "batch-1"}, headers=WRITE
  )
  assert response.status_code == 201
  assert [j.argv for j in jobs(made)] == [("run-item", "--go-on", "batch-1")]


def test_stopped_is_refused_while_a_new_attempt_with_no_record_holds_the_lock(
  made: QueueTree, static: Path
) -> None:
  web = client(made, static)
  write(made, "task-2", None, 0)
  # Newer than task-2, it has not written its record yet.
  fresh = made.tree.root / "batch-1" / "task-9"
  fresh.mkdir()
  then = time.time() + 10
  os.utime(fresh, (then, then))

  with holding(made.tree.root / "batch-1" / ".lock"):
    held = reasons(web)["go-on"]

  assert held == "An attempt of this item is running: its lock is held."


def test_stopped_takes_the_attempt_that_started_last_as_the_newest(
  made: QueueTree, static: Path
) -> None:
  web = client(made, static)
  # task-0 started first and was written last; task-2 started last and ended.
  write(made, "task-0", None, 0, started="2026-01-01T00:00:00+00:00")
  write(made, "task-2", "passed", 3600, started="2026-01-03T00:00:00+00:00")
  ended = reasons(web)["go-on"]
  write(made, "task-2", None, 3600, started="2026-01-03T00:00:00+00:00")
  stopped = reasons(web)["go-on"]
  response = web.get(ACTIONS, params={"item": "batch-1"})

  assert ended == (
    "Go on needs an attempt that may have stopped; the newest, task-2,"
    + " ended Passed."
  )
  assert stopped is None
  assert cast(dict[str, object], response.json())["newest"] == "batch-1/task-2"


def test_the_actions_name_the_newest_attempt_they_go_on_with(
  made: QueueTree, static: Path
) -> None:
  web = client(made, static)

  def newest() -> object:
    response = web.get(ACTIONS, params={"item": "batch-1"})
    return cast(dict[str, object], response.json())["newest"]

  write(made, "task-0", "passed", 3600)
  first = newest()
  write(made, "task-2", "passed", 0)
  later = newest()
  # A newer record that cannot be read is not the one.
  unread = made.tree.root / "batch-1" / "task-3" / "record.json"
  unread.parent.mkdir()
  _ = unread.write_text("{not json")
  past_unread = newest()

  assert [first, later, past_unread] == [
    "batch-1/task-1",
    "batch-1/task-2",
    "batch-1/task-2",
  ]


def test_stopped_goes_on_with_a_resumable_status_unless_the_lock_is_held(
  made: QueueTree, static: Path
) -> None:
  project = made.tree.project
  _ = project.write_text(
    project.read_text().replace(
      "  passed: {label: Passed, tone: good}\n",
      "  passed: {label: Passed, tone: good}\n"
      + "  canceled: {label: Canceled, tone: warn}\n",
    )
    + "resumable: [canceled]\n"
  )
  web = client(made, static)
  lock = made.tree.root / "batch-1" / ".lock"
  write(made, "task-1", "passed", 0)
  passed = reasons(web)["go-on"]
  write(made, "task-1", "canceled", 0)
  with holding(lock):
    running = reasons(web)["go-on"]
  canceled = reasons(web)["go-on"]

  assert passed == (
    "Go on needs an attempt that may have stopped or ended Canceled; the"
    + " newest, task-1, ended Passed."
  )
  assert running == "An attempt of this item is running: its lock is held."
  assert canceled is None
  listing = cast(
    list[dict[str, object]],
    web.get("/api/projects/example/attempts").json(),
  )
  assert [r["live"] for r in listing] == [None]
  response = web.post(
    f"{ACTIONS}/go-on", params={"item": "batch-1"}, headers=WRITE
  )
  assert response.status_code == 201


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
    {"name": "fast", "limit": 2, "running": 0, "queued": 1, "paused": None},
    {"name": "slow", "limit": 1, "running": 0, "queued": 0, "paused": None},
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
  assert web.get(ACTIONS, params={"item": "batch-1"}).json() == {
    "newest": None,
    "actions": [],
  }
  assert web.post("/api/queue/jobs/1/cancel", headers=WRITE).status_code == 404
  assert (
    web.post(
      f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE
    ).status_code
    == 404
  )


def test_the_queue_s_reads_write_nothing(made: QueueTree, static: Path) -> None:
  with_jobs(made)
  web = client(made, static)
  write(made, "task-2", None, 0)
  _ = web.post(f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE)
  _ = queue.pause(config(made), "slow")
  before = snapshot(made.queue.parent)
  urls = [
    "/api/queue",
    f"{ACTIONS}?item=batch-1",
    f"{TABS}?attempt=batch-1/task-1",
    "/api/projects/example/attempts",
  ]

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


def test_an_action_s_parameters_go_into_its_command_as_flags(
  made: QueueTree, static: Path
) -> None:
  with_params(made)
  web = client(made, static)

  [shown, _] = cast(
    dict[str, list[dict[str, object]]],
    web.get(ACTIONS, params={"item": "batch-1"}).json(),
  )["actions"]
  set_ = web.post(
    f"{ACTIONS}/again",
    params={"item": "batch-1", "budget": "12.50", "tries": "-0.25"},
    headers=WRITE,
  )
  _ = web.post(f"/api/queue/jobs/{jobs(made)[0].id}/cancel", headers=WRITE)
  default = web.post(
    f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE
  )

  # The page reads each parameter but its flag, which is the command's.
  assert shown["params"] == [
    {
      "name": "budget",
      "label": "Budget",
      "min": 1,
      "max": 150,
      "default": 75,
      "format": "usd",
    },
    {
      "name": "tries",
      "label": "Tries",
      "min": -2,
      "max": 0.5,
      "default": 0,
      "format": "number",
    },
  ]
  assert (set_.status_code, default.status_code) == (201, 201)
  first, second = sorted(jobs(made), key=lambda j: j.id)
  base = ("run-item", "--item", "batch-1", "--note=from batch-1")
  assert first.argv == (*base, "--max-usd=12.5", "--tries=-0.25")
  assert second.argv == (*base, "--max-usd=75", "--tries=0")
  assert first.labels == {
    "project": "example",
    "action": "again",
    "item": "batch-1",
  }


@pytest.mark.parametrize(
  ("value", "why"),
  [
    ("0.99", "is not from 1 to 150"),
    ("150.01", "is not from 1 to 150"),
    ("-75", "is not from 1 to 150"),
    ("1e2", "is not a decimal number"),
    ("+75", "is not a decimal number"),
    ("75.", "is not a decimal number"),
    (".5", "is not a decimal number"),
    (" 75", "is not a decimal number"),
    ("75\n", "is not a decimal number"),
    ("7_5", "is not a decimal number"),
    ("0x4b", "is not a decimal number"),
    ("\uff17\uff15", "is not a decimal number"),
    ("NaN", "is not a decimal number"),
    ("Infinity", "is not a decimal number"),
    ("", "is not a decimal number"),
    ("--help", "is not a decimal number"),
    ("75 --tries=9", "is not a decimal number"),
    ("1" + "0" * 20, "is not a decimal number"),
    ("75." + "0" * 12, "is not a decimal number"),
    ("1.0000001", "is not a decimal number"),
  ],
)
def test_a_value_that_is_not_a_decimal_within_bounds_is_refused(
  made: QueueTree, static: Path, value: str, why: str
) -> None:
  with_params(made)
  web = client(made, static)

  response = web.post(
    f"{ACTIONS}/again",
    params={"item": "batch-1", "budget": value},
    headers=WRITE,
  )

  assert response.status_code == 400
  assert why in cast(dict[str, str], response.json())["reason"]
  assert jobs(made) == []


@pytest.mark.parametrize(
  ("value", "arg"),
  [
    ("1", "--max-usd=1"),
    ("150", "--max-usd=150"),
    ("150.000", "--max-usd=150"),
    ("075", "--max-usd=75"),
    ("1.000000", "--max-usd=1"),
    ("149.999999", "--max-usd=149.999999"),
    ("100.10", "--max-usd=100.1"),
  ],
)
def test_a_value_at_a_bound_or_padded_is_taken_plain(
  made: QueueTree, static: Path, value: str, arg: str
) -> None:
  with_params(made)

  response = client(made, static).post(
    f"{ACTIONS}/again",
    params={"item": "batch-1", "budget": value},
    headers=WRITE,
  )

  assert response.status_code == 201
  [job] = jobs(made)
  assert job.argv[-2:] == (arg, "--tries=0")


@pytest.mark.parametrize(
  ("action", "params"),
  [
    ("again", {"item": "batch-1", "budget2": "75"}),
    ("again", {"item": "batch-1", "max-usd": "75"}),
    ("again", [("item", "batch-1"), ("budget", "75"), ("budget", "9")]),
    ("go-on", {"item": "batch-1", "budget": "75"}),
  ],
)
def test_a_parameter_the_action_does_not_declare_is_refused(
  made: QueueTree, static: Path, action: str, params: object
) -> None:
  with_params(made)

  response = client(made, static).post(
    f"{ACTIONS}/{action}",
    params=cast(dict[str, str], params),
    headers=WRITE,
  )

  assert response.status_code == 400
  assert jobs(made) == []


JOBS = """\
jobs:
  - {action: review, label: Review, queue: slow, subject: attempt}
  - {action: batch, label: Batch, queue: fast}
tabs:
  - {name: review, title: Review, versions: ["review*"], file: r.md,
     job: review}
columns:
  - {title: Verdict, tab: review, file: r.json, field: [verdict]}
"""


def with_jobs(made: QueueTree) -> None:
  text = made.tree.project.read_text()
  # The project's own columns give way to one of the tab's.
  head, tail = text.split("columns:\n")
  rest = tail[tail.index("lock:") :]
  _ = made.tree.project.write_text(head + rest + JOBS)


def other(made: QueueTree, labels: dict[str, str], name: str = "slow") -> int:
  return queue.submit(
    config(made),
    name,
    ["elsewhere"],
    cwd=made.work,
    memory_mib=1,
    key=None,
    labels=labels,
  ).id


def test_a_tab_gives_its_job_s_newest_for_the_attempt_and_its_queue_s_pause(
  made: QueueTree, static: Path
) -> None:
  with_jobs(made)
  attempt = "batch-1/task-1"
  older = other(made, {"action": "review", "item": attempt})
  newer = other(made, {"action": "review", "item": attempt})
  _ = other(made, {"action": "review", "item": "batch-1/task-2"})
  _ = other(made, {"project": "another", "action": "review", "item": attempt})
  _ = queue.cancel(config(made), newer)
  _ = queue.pause(config(made), "slow", "held for now")
  web = client(made, static)

  [tab] = cast(
    list[dict[str, object]],
    web.get("/api/projects/example/tabs", params={"attempt": attempt}).json(),
  )

  found = cast(dict[str, object], tab["queued"])
  job = cast(dict[str, object], found["job"])
  assert (job["id"], job["state"]) == (newer, "canceled")
  assert older != newer
  paused = cast(dict[str, object], found["paused"])
  assert (paused["queue"], paused["reason"]) == ("slow", "held for now")
  assert found["reason"] is None


def test_a_tab_with_no_job_yet_says_whether_its_queue_is_paused(
  made: QueueTree, static: Path
) -> None:
  with_jobs(made)
  web = client(made, static)
  params = {"attempt": "batch-1/task-1"}

  before = cast(object, web.get(TABS, params=params).json())
  _ = queue.pause(config(made), "slow")
  after = cast(object, web.get(TABS, params=params).json())

  assert before == [
    {
      "name": "review",
      "queued": {"job": None, "paused": None, "reason": None},
      "versions": [],
    }
  ]
  [tab] = cast(list[dict[str, object]], after)
  queued = cast(dict[str, dict[str, object]], tab["queued"])
  assert queued["paused"]["queue"] == "slow"


def test_a_column_of_a_tab_with_no_version_gives_the_tab_s_job(
  made: QueueTree, static: Path
) -> None:
  with_jobs(made)
  write(made, "task-2", "passed", 0)
  done = made.tree.root / "batch-1" / "task-2" / "review-1"
  done.mkdir()
  _ = (done / "r.json").write_text('{"verdict": "fine"}')
  write(made, "task-3", "passed", 0)
  # A version with no file yet is not run, but the tab has one: no job.
  (made.tree.root / "batch-1" / "task-3" / "review-1").mkdir()
  job = other(made, {"action": "review", "item": "batch-1/task-1"})
  for attempt in ("batch-1/task-2", "batch-1/task-3"):
    _ = other(made, {"action": "review", "item": attempt})
  web = client(made, static)

  rows = cast(
    list[dict[str, object]], web.get("/api/projects/example/attempts").json()
  )

  found = {cast(str, r["id"]): r for r in rows}
  ran, waits = found["batch-1/task-2"], found["batch-1/task-1"]
  assert (ran["reads"], ran["queued"]) == (["found"], [None])
  started = found["batch-1/task-3"]
  assert (started["reads"], started["queued"]) == (["not run"], [None])
  assert waits["reads"] == ["not run"]
  [queued] = cast(list[dict[str, dict[str, object]]], waits["queued"])
  assert (queued["job"]["id"], queued["job"]["state"]) == (job, "queued")


def test_the_queue_says_which_queues_are_paused(
  made: QueueTree, static: Path
) -> None:
  _ = queue.pause(config(made), "slow", "held for now")

  body = cast(dict[str, object], client(made, static).get("/api/queue").json())

  queues = cast(list[dict[str, object]], body["queues"])
  assert [q["paused"] is None for q in queues] == [True, False]
  paused = cast(dict[str, object], queues[1]["paused"])
  assert (paused["queue"], paused["reason"]) == ("slow", "held for now")


def test_a_job_with_no_project_label_is_the_project_s_that_names_its_action(
  made: QueueTree,
) -> None:
  with_jobs(made)
  [project] = load_site(made.tree.site).projects
  ids = [
    other(made, {"action": "review"}),
    other(made, {"action": "elsewhere"}),
    other(made, {"project": "example", "action": "elsewhere"}),
    other(made, {"project": "another", "action": "review"}),
  ]

  found = {j.id: actions.of_project(project, j) for j in jobs(made)}

  assert [found[i] for i in ids] == [True, False, True, False]


@pytest.mark.parametrize(
  ("change", "key"),
  [
    (("action: batch,", "action: review,"), r"jobs\[1\]\.action: 'review' rep"),
    (("action: batch,", "action: again,"), r"jobs\[1\]\.action: 'again' rep"),
    (("queue: fast}", "queue: fast, subject: run}"), r"jobs\[1\]\.subject"),
    (("queue: fast}", "queue: fast, item: x}"), r"jobs\[1\]\.item: unknown"),
    (("label: Batch, ", ""), r"jobs\[1\]\.label: missing"),
    (("queue: fast}", "queue: none}"), r"jobs\[1\]\.queue: 'none' is not in"),
    (("job: review}", "job: batch}"), r"tabs\[0\]\.job: 'batch' is not a job"),
  ],
)
def test_a_job_that_breaks_a_rule_is_refused(
  made: QueueTree, change: tuple[str, str], key: str
) -> None:
  with_jobs(made)
  old, new = change
  text = made.tree.project.read_text()
  assert old in text
  _ = made.tree.project.write_text(text.replace(old, new, 1))

  with pytest.raises(ConfigError, match=key):
    _ = load_site(made.tree.site)


def test_a_project_s_jobs_load(made: QueueTree) -> None:
  with_jobs(made)

  [project] = load_site(made.tree.site).projects

  assert project.public()["jobs"] == [
    {
      "action": "review",
      "label": "Review",
      "queue": "slow",
      "subject": "attempt",
    },
    {"action": "batch", "label": "Batch", "queue": "fast", "subject": "item"},
  ]
  [tab] = cast(list[dict[str, object]], project.public()["tabs"])
  assert tab["job"] == "review"


def test_a_tab_whose_queue_cannot_be_read_says_why(
  made: QueueTree, static: Path
) -> None:
  with_jobs(made)
  _ = (made.queue / "queue.sqlite").write_bytes(b"not a database, " * 64)
  web = client(made, static)

  [tab] = cast(
    list[dict[str, object]],
    web.get(TABS, params={"attempt": "batch-1/task-1"}).json(),
  )
  [row] = cast(
    list[dict[str, object]], web.get("/api/projects/example/attempts").json()
  )

  queued = cast(dict[str, object], tab["queued"])
  assert (queued["job"], queued["paused"]) == (None, None)
  assert str(queued["reason"]).startswith("the queue cannot be read: ")
  assert row["queued"] == [queued]


def ran(made: QueueTree, state: str, start: float, end: float) -> int:
  # A job of batch-1's, queued as the page queues one, that ran from start
  # to end and ended in state.
  web = TestClient(
    create_app(load_site(made.tree.site), made.work), base_url=ORIGIN
  )
  _ = web.post(f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE)
  job = jobs(made)[0]
  with closing(
    sqlite3.connect(made.queue / "queue.sqlite", autocommit=True)
  ) as conn:
    _ = conn.execute(
      "UPDATE jobs SET state = ?, started_at = ?, ended_at = ? WHERE id = ?",
      (state, start, end, job.id),
    )
  return job.id


def stopped_of(made: QueueTree, static: Path, name: str) -> tuple[object, ...]:
  web = client(made, static)
  rows = cast(
    list[dict[str, object]], web.get("/api/projects/example/attempts").json()
  )
  [row] = [r for r in rows if r["id"] == f"batch-1/{name}"]
  one = cast(
    dict[str, object],
    web.get(
      "/api/projects/example/live", params={"attempt": f"batch-1/{name}"}
    ).json(),
  )
  return row["live"], row["stopped"], one["stopped"]


def test_an_attempt_whose_job_has_ended_has_stopped(
  made: QueueTree, static: Path
) -> None:
  write(made, "task-2", None, 50)
  wrote = (made.tree.root / "batch-1" / "task-2" / "record.json").stat()
  job = ran(made, "failed", wrote.st_mtime - 100, wrote.st_mtime + 10)

  found = stopped_of(made, static, "task-2")

  stopped = {"job": job, "state": "failed"}
  assert found == ("may have stopped", stopped, stopped)


def test_an_attempt_no_ended_job_ran_may_have_stopped(
  made: QueueTree, static: Path
) -> None:
  write(made, "task-2", None, 50)
  wrote = (made.tree.root / "batch-1" / "task-2" / "record.json").stat()
  # The job ended before the record was last written: it did not run it.
  _ = ran(made, "canceled", wrote.st_mtime - 100, wrote.st_mtime - 10)

  assert stopped_of(made, static, "task-2") == ("may have stopped", None, None)


def test_an_attempt_whose_items_lock_is_held_has_not_stopped_for_sure(
  made: QueueTree, static: Path
) -> None:
  write(made, "task-2", None, 50)
  write(made, "task-3", None, 10)
  wrote = (made.tree.root / "batch-1" / "task-2" / "record.json").stat()
  _ = ran(made, "done", wrote.st_mtime - 100, wrote.st_mtime + 10)

  with holding(made.tree.root / "batch-1" / ".lock"):
    found = stopped_of(made, static, "task-2")

  assert found == ("may have stopped", None, None)

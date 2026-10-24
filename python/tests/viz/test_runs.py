from collections.abc import Callable
from contextlib import closing
import dataclasses
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from typing import cast

from fastapi.testclient import TestClient
import pytest

from dataforge import queue
from dataforge.queue.proc import Leader, leader
from dataforge.runs import AttemptRow, SCHEMA_MAJOR, Store
from dataforge.runs.store import ListingRow
from dataforge.viz.app import create_app
from dataforge.viz.config import load_site
from dataforge.viz.runs import HOLDER_GONE, NO_ROW
from tests.processes import WAIT
from tests.viz.sites import FIXTURES, make_queue_tree, QueueTree

ORIGIN = "http://127.0.0.1:8800"
WRITE = {"X-Viz-Write": "1", "Origin": ORIGIN}
ATTEMPTS = "/api/projects/example/attempts"
ACTIONS = "/api/projects/example/actions"
LIVE = "/api/projects/example/live"
RUNS = "runs: {db: ../runs.sqlite, stopped_by: {person: canceled}}\n"
ENDED_AT = "2026-01-02T04:00:00+00:00"


@pytest.fixture
def made(tmp_path: Path) -> QueueTree:
  made = make_queue_tree(tmp_path)
  project = made.tree.project
  text = project.read_text()
  assert "lock: .lock\n" in text
  _ = project.write_text(text.replace("lock: .lock\n", RUNS))
  return made


@pytest.fixture
def store(made: QueueTree) -> Store:
  return Store(made.tree.root.parent / "runs.sqlite")


@pytest.fixture
def static(tmp_path: Path) -> Path:
  dist = tmp_path / "dist"
  dist.mkdir()
  _ = (dist / "index.html").write_text("<!doctype html>")
  return dist


@pytest.fixture
def me() -> Leader:
  found = leader(os.getpid())
  assert found is not None
  return found


@pytest.fixture
def gone() -> Leader:
  # A process that ran and is gone, as a holder.
  with subprocess.Popen(["sleep", "60"]) as process:
    found = leader(process.pid)
    process.kill()
    _ = process.wait(WAIT)
  assert found is not None
  return found


def client(made: QueueTree, static: Path) -> TestClient:
  return TestClient(
    create_app(load_site(made.tree.site), static), base_url=ORIGIN
  )


def attempt(made: QueueTree, name: str, record: dict[str, object]) -> str:
  # An attempt directory of batch-1 whose record file holds the fixture's
  # record with these keys over it; its ID.
  fixture = cast(
    dict[str, object], json.loads((FIXTURES / "record.json").read_text())
  )
  path = made.tree.root / "batch-1" / name / "record.json"
  path.parent.mkdir(exist_ok=True)
  _ = path.write_text(json.dumps(fixture | record))
  return f"batch-1/{name}"


def kept(attempt_id: str, state: str, **fields: object) -> AttemptRow:
  # A row as Store.add takes it, the fields given over these.
  row = AttemptRow(
    id=attempt_id,
    item="batch-1",
    root=f"/attempts/{attempt_id}",
    state=state,
    outcome=None,
    reason=None,
    machine_status=None,
    stopped_by=None,
    record=None,
    cost_usd=None,
    progress_at=None,
    holder=None,
    job_id=None,
    job_db=None,
    resumable=None,
    resume_problem=None,
    resume_checked_at=None,
    created_at="2026-01-02T00:00:00+00:00",
    ended_at=None,
    settled_at=None,
  )
  return dataclasses.replace(row, **fields)


def add(store: Store, row: AttemptRow) -> None:
  assert store.add(row)


def listing(web: TestClient) -> dict[str, dict[str, object]]:
  response = web.get(ATTEMPTS)
  assert response.status_code == 200
  return {
    str(r["id"]): r for r in cast(list[dict[str, object]], response.json())
  }


def reasons(web: TestClient) -> dict[str, str | None]:
  response = web.get(ACTIONS, params={"item": "batch-1"})
  assert response.status_code == 200
  body = cast(dict[str, list[dict[str, str | None]]], response.json())
  return {str(r["name"]): r["reason"] for r in body["actions"]}


def test_an_attempt_s_status_reason_cost_and_live_come_from_its_row(
  made: QueueTree, static: Path, store: Store, me: Leader
) -> None:
  # Each record file says another thing: the rows win.
  ended = attempt(made, "task-1", {"status": "running"})
  person = attempt(made, "task-2", {"status": "running"})
  lost = attempt(made, "task-3", {"status": "passed"})
  running = attempt(made, "task-4", {"status": "canceled"})
  add(
    store,
    kept(
      ended,
      "ended",
      outcome="too_easy",
      reason="solved by every trial",
      cost_usd=3.5,
      progress_at="2026-01-02T03:00:00+00:00",
      ended_at=ENDED_AT,
    ),
  )
  add(store, kept(person, "interrupted", stopped_by="person", cost_usd=1.25))
  add(store, kept(lost, "interrupted", stopped_by="lost"))
  assert store.insert(running, "batch-2", Path("/r"), me, None)

  rows = listing(client(made, static))

  found = {
    i: (r["status"], r["reason"], r["cost_usd"], r["live"], r["state"])
    for i, r in rows.items()
  }
  assert found == {
    ended: ("too_easy", "solved by every trial", 3.5, None, "ended"),
    person: ("canceled", None, 1.25, None, "interrupted"),
    lost: ("interrupted", None, None, "may have stopped", "interrupted"),
    running: (None, None, None, "running", "running"),
  }
  assert {i: r["cost_from"] for i, r in rows.items()} == {
    ended: "store",
    person: "store",
    lost: None,
    running: None,
  }
  # Only a running row whose holder is alive holds its item.
  assert {i: r["holder"] for i, r in rows.items()} == {
    ended: False,
    person: False,
    lost: False,
    running: True,
  }
  assert rows[ended]["progress_at"] == "2026-01-02T03:00:00+00:00"
  assert all(r["error"] is None and r["note"] is None for r in rows.values())
  assert all(r["held"] is None for r in rows.values())


def test_a_row_a_person_stopped_has_ended_at_its_newest_stop(
  made: QueueTree, static: Path, store: Store, me: Leader
) -> None:
  # A row a person stopped, gone on with and stopped again; then one lost.
  person = attempt(made, "task-1", {"status": "running"})
  lost = attempt(made, "task-2", {"status": "running"})
  assert store.insert(person, "batch-1", Path("/r"), me, None)
  assert store.interrupt(person, me, by="runner", stopped_by="person")
  assert store.take(person, me, None, by="runner")
  assert store.interrupt(person, me, by="runner", stopped_by="person")
  assert store.insert(lost, "batch-1", Path("/r"), me, None)
  assert store.interrupt(lost, me, by="reconcile", stopped_by="lost")
  stops = [e.at for e in store.events(person) if e.to_state == "interrupted"]
  web = client(made, static)

  rows = listing(web)
  live = cast(
    dict[str, object], web.get(LIVE, params={"attempt": person}).json()
  )

  assert len(stops) == 2 and stops[0] < stops[1]
  assert (
    rows[person]["status"],
    rows[person]["live"],
    rows[person]["state"],
    rows[person]["ended_at"],
  ) == ("canceled", None, "interrupted", stops[1])
  assert live["live"] is None
  # One lost may have stopped, and its end is its record's, as before.
  assert (rows[lost]["status"], rows[lost]["live"]) == (
    "interrupted",
    "may have stopped",
  )
  assert rows[lost]["ended_at"] not in stops


def test_a_row_a_person_stopped_before_its_first_visit_starts_with_its_row(
  made: QueueTree, static: Path, store: Store, me: Leader
) -> None:
  stopped = attempt(made, "task-1", {"status": "running", "visits": []})
  assert store.insert(stopped, "batch-1", Path("/r"), me, None)
  assert store.interrupt(stopped, me, by="runner", stopped_by="person")
  row = store.get(stopped)
  assert row is not None

  listed = listing(client(made, static))[stopped]

  assert (listed["live"], listed["started_at"]) == (None, row.created_at)


def test_a_running_row_whose_holder_is_gone_says_so(
  made: QueueTree, static: Path, store: Store, gone: Leader
) -> None:
  running = attempt(made, "task-1", {"status": "running"})
  assert store.insert(running, "batch-1", Path("/r"), gone, None)
  web = client(made, static)

  row = listing(web)[running]
  live = cast(object, web.get(LIVE, params={"attempt": running}).json())

  assert (row["live"], row["state"], row["note"], row["holder"]) == (
    "may have stopped",
    "running",
    HOLDER_GONE,
    False,
  )
  assert live == {
    "live": "may have stopped",
    "held": None,
    "holder": False,
    "stopped": None,
    "current": None,
    "state": "running",
    "status": None,
    "reason": None,
    "note": HOLDER_GONE,
  }


def test_an_interrupted_row_whose_machine_ended_says_so(
  made: QueueTree, static: Path, store: Store
) -> None:
  stopped = attempt(made, "task-1", {"status": "running"})
  add(
    store,
    kept(stopped, "interrupted", stopped_by="person", machine_status="passed"),
  )
  web = client(made, static)

  row = listing(web)[stopped]

  assert (row["status"], row["live"], row["note"]) == (
    "canceled",
    None,
    "its machine ended passed; a reconcile has not ended it yet",
  )
  assert reasons(web)["go-on"] == (
    "Its machine ended passed; a reconcile has not ended it yet."
  )


def test_a_directory_with_no_row_is_read_from_its_record_and_says_so(
  made: QueueTree, static: Path, store: Store
) -> None:
  other = attempt(made, "task-2", {"status": "passed"})
  add(store, kept(other, "ended", outcome="failed", ended_at=ENDED_AT))

  rows = listing(client(made, static))

  # The fixture's attempt, batch-1/task-1, has no row.
  alone = rows["batch-1/task-1"]
  assert (alone["status"], alone["cost_usd"], alone["error"]) == (
    "passed",
    0.42,
    NO_ROW,
  )
  assert (alone["state"], alone["holder"]) == (None, None)
  assert rows[other]["status"] == "failed"


def bare(made: QueueTree, name: str) -> str:
  # An attempt directory of batch-1 with no record file; its ID.
  path = made.tree.root / "batch-1" / name
  path.mkdir()
  return f"batch-1/{name}"


def test_a_row_with_no_record_file_shows_its_row_and_no_error(
  made: QueueTree, static: Path, store: Store
) -> None:
  ended = bare(made, "task-2")
  add(store, kept(ended, "ended", outcome="failed", ended_at=ENDED_AT))

  row = listing(client(made, static))[ended]

  assert (row["status"], row["state"], row["error"]) == (
    "failed",
    "ended",
    None,
  )


def test_the_live_route_gives_the_row_of_an_attempt_with_no_record_file(
  made: QueueTree, static: Path, store: Store
) -> None:
  ended = bare(made, "task-2")
  add(store, kept(ended, "ended", outcome="failed", ended_at=ENDED_AT))

  response = client(made, static).get(LIVE, params={"attempt": ended})

  assert response.status_code == 200
  body = cast(dict[str, object], response.json())
  assert (body["state"], body["status"]) == ("ended", "failed")


def test_a_directory_with_no_row_and_no_record_file_says_it_has_no_row(
  made: QueueTree, static: Path, store: Store
) -> None:
  lone = bare(made, "task-2")
  other = attempt(made, "task-3", {"status": "passed"})
  add(store, kept(other, "ended", outcome="failed", ended_at=ENDED_AT))

  row = listing(client(made, static))[lone]

  assert (row["status"], row["error"]) == (None, NO_ROW)


def test_with_no_runs_a_missing_record_file_is_an_error(
  made: QueueTree, static: Path
) -> None:
  project = made.tree.project
  _ = project.write_text(project.read_text().replace(RUNS, "lock: .lock\n"))
  lone = bare(made, "task-2")

  row = listing(client(made, static))[lone]

  assert "No such file" in str(row["error"])


def test_with_no_store_yet_every_attempt_says_it_has_no_row(
  made: QueueTree, static: Path, store: Store
) -> None:
  rows = listing(client(made, static))

  assert [r["error"] for r in rows.values()] == [NO_ROW]
  assert not store.path.exists()


def test_a_store_of_a_newer_schema_major_is_not_read(
  made: QueueTree, static: Path, store: Store
) -> None:
  ended = attempt(made, "task-2", {"status": "passed"})
  add(store, kept(ended, "ended", outcome="failed", ended_at=ENDED_AT))
  with closing(sqlite3.connect(store.path)) as conn, conn:
    _ = conn.execute(
      "UPDATE meta SET value = ? WHERE name = 'schema_major'",
      (SCHEMA_MAJOR + 1,),
    )
  web = client(made, static)

  rows = listing(web)

  newer = (
    f"the store's schema_major {SCHEMA_MAJOR + 1} is newer than this"
    + f" viewer's {SCHEMA_MAJOR}"
  )
  assert {i: r["error"] for i, r in rows.items()} == {
    "batch-1/task-1": newer,
    ended: newer,
  }
  # Read from its record, not its row.
  assert rows[ended]["status"] == "passed"
  assert reasons(web)["go-on"] == (
    f"The store of attempts cannot be read: {newer}."
  )


def test_the_listing_reads_no_record_or_extra(
  made: QueueTree, static: Path, store: Store
) -> None:
  # The two JSON columns hold what no JSON parser reads; the listing does not
  # parse them, so it gives the row's own columns.
  ended = attempt(made, "task-1", {"status": "running"})
  add(
    store,
    kept(
      ended,
      "ended",
      outcome="too_easy",
      cost_usd=2.0,
      record={"status": "passed"},
      ended_at=ENDED_AT,
    ),
  )
  with closing(sqlite3.connect(store.path)) as conn, conn:
    _ = conn.execute("UPDATE attempt SET record = '{', extra = 'not json'")

  rows = listing(client(made, static))

  assert rows[ended]["status"] == "too_easy"
  assert rows[ended]["cost_usd"] == 2.0
  assert rows[ended]["error"] is None


def test_the_listing_marks_as_the_whole_rows_do(
  made: QueueTree,
  static: Path,
  store: Store,
  me: Leader,
  gone: Leader,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  ended = attempt(made, "task-1", {"status": "running"})
  person = attempt(made, "task-2", {"status": "running"})
  lost = attempt(made, "task-3", {"status": "passed"})
  running = attempt(made, "task-4", {"status": "canceled"})
  dead = attempt(made, "task-5", {"status": "running"})
  record = {"status": "passed", "budget": {"cost_usd": 3.5}}
  add(
    store,
    kept(
      ended,
      "ended",
      outcome="too_easy",
      reason="solved",
      record=record,
      cost_usd=3.5,
      progress_at="2026-01-02T03:00:00+00:00",
      ended_at=ENDED_AT,
      extra={"k": "v"},
    ),
  )
  add(store, kept(person, "interrupted", stopped_by="person", cost_usd=1.25))
  add(store, kept(lost, "interrupted", stopped_by="lost", machine_status="x"))
  assert store.insert(running, "batch-2", Path("/r"), me, None)
  assert store.insert(dead, "batch-1", Path("/r"), gone, None)
  web = client(made, static)

  fresh = listing(web)

  def whole(self: Store, *, item: str | None = None) -> list[ListingRow]:
    return [
      ListingRow(
        id=row.id,
        item=row.item,
        state=row.state,
        outcome=row.outcome,
        reason=row.reason,
        machine_status=row.machine_status,
        stopped_by=row.stopped_by,
        cost_usd=row.cost_usd,
        progress_at=row.progress_at,
        holder=row.holder,
        resumable=row.resumable,
        resume_problem=row.resume_problem,
        created_at=row.created_at,
      )
      for row in self.rows(item=item)
    ]

  monkeypatch.setattr(Store, "listing", whole)
  assert listing(web) == fresh
  assert len(fresh) == 5


def test_the_store_is_opened_read_only_and_never_written(
  made: QueueTree,
  static: Path,
  store: Store,
  me: Leader,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  # A row a person stopped, whose events are read too, and one running.
  stopped = attempt(made, "task-1", {"status": "running"})
  assert store.insert(stopped, "batch-1", Path("/r"), me, None)
  assert store.interrupt(stopped, me, by="runner", stopped_by="person")
  running = attempt(made, "task-2", {"status": "running"})
  assert store.insert(running, "batch-1", Path("/r"), me, None)
  web = client(made, static)
  opened: list[str] = []
  connect = cast(Callable[..., sqlite3.Connection], sqlite3.connect)

  def spy(database: object, *args: object, **kwargs: object) -> object:
    opened.append(str(database))
    return connect(database, *args, **kwargs)

  monkeypatch.setattr(sqlite3, "connect", spy)
  before = snapshot(store.path.parent)
  urls = [ATTEMPTS, f"{LIVE}?attempt={running}", f"{ACTIONS}?item=batch-1"]

  for url in urls:
    assert web.get(url).status_code == 200, url

  stores = [o for o in opened if store.path.name in o]
  assert stores
  assert all(o.startswith("file:") and o.endswith("?mode=ro") for o in stores)
  assert snapshot(store.path.parent) == before


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
  # SQLite's own -wal and -shm, which a reader of a WAL database may make and
  # write, are left out.
  found: dict[str, tuple[int, int]] = {}
  for top, _dirs, files in os.walk(root):
    for name in files:
      if not name.endswith(("-wal", "-shm")):
        status = (Path(top) / name).lstat()
        found[str(Path(top) / name)] = (status.st_size, status.st_mtime_ns)
  return found


def test_stopped_needs_a_newest_row_interrupted_that_may_go_on(
  made: QueueTree, static: Path, store: Store
) -> None:
  web = client(made, static)
  # Rows of the item, each newer than the one before, and the reason each
  # gives while it is the newest.
  steps: list[tuple[AttemptRow, str | None]] = [
    (
      kept("batch-1/a", "ended", outcome="passed", ended_at=ENDED_AT),
      "Go on needs an attempt that was interrupted and can go on; the"
      + " newest, a, ended Passed.",
    ),
    (
      kept("batch-1/b", "interrupted", stopped_by="lost"),
      "Not checked yet whether it can go on.",
    ),
    (
      kept(
        "batch-1/c",
        "interrupted",
        stopped_by="person",
        resumable=False,
        resume_problem="its session container is gone",
      ),
      "its session container is gone",
    ),
    (
      kept("batch-1/d", "interrupted", stopped_by="person", resumable=True),
      None,
    ),
  ]
  empty = reasons(web)["go-on"]
  found: list[str | None] = []
  for i, (row, _) in enumerate(steps):
    created = f"2026-01-02T0{i}:00:00+00:00"
    add(store, dataclasses.replace(row, created_at=created))
    found.append(reasons(web)["go-on"])

  assert empty == "The store has no attempt of this item."
  assert found == [reason for _, reason in steps]
  # The attempt it goes on with is the store's newest row.
  newest = cast(
    dict[str, object], web.get(ACTIONS, params={"item": "batch-1"}).json()
  )["newest"]
  assert newest == "batch-1/d"
  response = web.post(
    f"{ACTIONS}/go-on", params={"item": "batch-1"}, headers=WRITE
  )
  assert response.status_code == 201
  assert [j.argv for j in queue.jobs(_config(made))] == [
    ("run-item", "--go-on", "batch-1")
  ]


def _config(made: QueueTree) -> queue.Config:
  return queue.load_config(made.queue / "queue.yaml")


def test_stopped_says_how_the_newest_row_ended_and_why(
  made: QueueTree, static: Path, store: Store
) -> None:
  # A word the project has no label for shows as itself, and the row's
  # reason follows it.
  add(
    store,
    kept(
      "batch-1/2026-01-02T000000Z",
      "ended",
      outcome="too_easy",
      reason="solved by every trial.",
      ended_at=ENDED_AT,
    ),
  )

  found = reasons(client(made, static))["go-on"]

  assert found == (
    "Go on needs an attempt that was interrupted and can go on; the newest,"
    + " 2026-01-02T000000Z, ended too_easy: solved by every trial."
  )


def test_stopped_and_idle_wait_while_a_row_of_the_item_runs(
  made: QueueTree, static: Path, store: Store, me: Leader
) -> None:
  web = client(made, static)
  add(
    store,
    kept(
      "batch-1/a",
      "interrupted",
      stopped_by="person",
      resumable=True,
    ),
  )
  free = reasons(web)
  assert store.insert("batch-1/b", "batch-1", Path("/r"), me, None)

  running = reasons(web)
  refused = web.post(
    f"{ACTIONS}/again", params={"item": "batch-1"}, headers=WRITE
  )

  assert free == {"again": None, "go-on": None}
  assert running == {
    "again": "An attempt of this item is running.",
    "go-on": "The newest attempt of this item is running.",
  }
  assert refused.status_code == 409
  assert refused.json()["reason"] == "An attempt of this item is running."


def test_the_project_gives_its_stopped_by_words_and_not_its_store(
  made: QueueTree, static: Path
) -> None:
  [project] = cast(
    list[dict[str, object]], client(made, static).get("/api/projects").json()
  )

  assert project["runs"] == {"stopped_by": {"person": "canceled"}}
  assert "runs.sqlite" not in json.dumps(project)

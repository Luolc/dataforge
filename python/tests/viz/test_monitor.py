import os
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
import pytest

from dataforge.viz import monitor
from dataforge.viz.app import create_app
from dataforge.viz.config import load_site
from dataforge.viz.paths import RefusedError
from tests.viz.monitor_db import make_db, START
from tests.viz.sites import make_tree, PROJECT, Tree

API = "/api/projects/example/monitor"


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
  tree = make_tree(tmp_path)
  _ = tree.project.write_text(PROJECT + "monitor: {db: ../monitor.sqlite}\n")
  return tree


@pytest.fixture
def db(tree: Tree) -> Path:
  path = tree.root.parent / "monitor.sqlite"
  make_db(path)
  return path


@pytest.fixture
def web(tree: Tree, tmp_path: Path) -> TestClient:
  static = tmp_path / "dist"
  static.mkdir()
  _ = (static / "index.html").write_text("<!doctype html>")
  return TestClient(create_app(load_site(tree.site), static))


def files(directory: Path) -> dict[str, bytes]:
  return {
    str(p.relative_to(directory)): p.read_bytes()
    for p in sorted(directory.rglob("*"))
    if p.is_file()
  }


def test_the_runs_are_listed_oldest_first(db: Path, web: TestClient) -> None:
  del db
  runs = cast(list[dict[str, object]], web.get(f"{API}/runs").json())

  assert [(r["run_id"], r["source"]) for r in runs] == [
    (1, "standalone"),
    (2, "batch"),
  ]


def test_a_window_reads_the_whole_run_by_default(
  db: Path, web: TestClient
) -> None:
  del db
  window = cast(dict[str, object], web.get(f"{API}/window?run=2").json())

  assert window["t0"] == START + 3600
  assert window["cpus"] == 32
  assert len(cast(list[object], window["machine"])) == monitor.POINTS
  assert len(cast(list[object], window["pauses"])) == 2
  bars = cast(list[dict[str, object]], window["bars"])
  assert window["bars_total"] == len(bars) > 0
  # The one container still running when the sampler stopped.
  assert [b["open"] for b in bars].count(1) == 1


def test_a_window_holds_what_overlaps_it(db: Path, web: TestClient) -> None:
  del db
  t0, t1 = START + 3600 + 600, START + 3600 + 900
  window = cast(
    dict[str, object],
    web.get(f"{API}/window", params={"run": 2, "t0": t0, "t1": t1}).json(),
  )

  bars = cast(list[dict[str, float]], window["bars"])
  assert bars
  assert all(b["start_t"] < t1 and b["last_t"] >= t0 for b in bars)
  times = [r["t"] for r in cast(list[dict[str, float]], window["machine"])]
  assert t0 <= min(times) and max(times) < t1


def test_the_bars_stop_at_the_limit_at_the_earliest_starts(
  db: Path, web: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  del db
  every = cast(
    list[dict[str, object]], web.get(f"{API}/window?run=2").json()["bars"]
  )
  starts = sorted(cast(float, b["start_t"]) for b in every)
  monkeypatch.setattr(monitor, "BARS_LIMIT", 5)

  window = cast(dict[str, object], web.get(f"{API}/window?run=2").json())

  bars = cast(list[dict[str, object]], window["bars"])
  # The five that start first, whatever their items, so that only the
  # window's last stretch is cut.
  assert sorted(cast(float, b["start_t"]) for b in bars) == starts[:5]
  assert window["bars_total"] == len(every)
  assert window["bars_until"] == starts[5]
  assert web.get(f"{API}/window?run=2").json() == window
  monkeypatch.setattr(monitor, "BARS_LIMIT", 5000)
  assert web.get(f"{API}/window?run=2").json()["bars_until"] is None


@pytest.mark.parametrize("by", ["attempt", "container"])
def test_a_curve_is_one_attempt_s_or_one_container_s(
  db: Path, web: TestClient, by: str
) -> None:
  del db
  window = cast(dict[str, object], web.get(f"{API}/window?run=2").json())
  bar = next(
    b
    for b in cast(list[dict[str, object]], window["bars"])
    if b["attempt"] == "batch-1/task-0002" and b["state"] == "solve"
  )
  key = cast(str, bar["attempt"] if by == "attempt" else bar["container_id"])
  t0, t1 = cast(float, window["t0"]), cast(float, window["t1"])
  params: dict[str, str | float] = {"run": 2, "t0": t0, "t1": t1, by: key}

  rows = cast(
    list[dict[str, float]], web.get(f"{API}/curve", params=params).json()
  )

  assert rows
  peak = max(r["cpu_cores_max"] for r in rows)
  # A trial's container uses at most 1.8 x 1.3 cores; the attempt, several.
  assert (peak > 2.4) == (by == "attempt")


@pytest.mark.parametrize(
  ("url", "status", "reason"),
  [
    (f"{API}/window", 400, "run is not a whole number"),
    (f"{API}/window?run=9", 404, "no monitor run 9"),
    (f"{API}/window?run=2&t0=nan", 400, "t0"),
    (f"{API}/window?run=2&t0=5&t1=4", 400, "ends before it starts"),
    (f"{API}/window?run=2&t0=1999999999", 400, "ends before it starts"),
    (f"{API}/curve?run=2&t0=1&t1=2", 400, "one of attempt and container"),
    ("/api/projects/other/monitor/runs", 404, "no project"),
  ],
)
def test_a_bad_request_is_refused_with_a_reason(
  db: Path, web: TestClient, url: str, status: int, reason: str
) -> None:
  del db
  response = web.get(url)

  assert response.status_code == status
  assert reason in cast(dict[str, str], response.json())["reason"]


def test_the_database_is_opened_read_only(db: Path) -> None:
  with pytest.raises(RefusedError) as refused, monitor.connect(db) as c:
    _ = c.execute("CREATE TABLE written (a)")

  assert refused.value.status == 503
  assert "readonly" in refused.value.reason
  assert "written" not in db.read_bytes().decode("latin-1")


def test_a_project_with_no_monitor_says_so(tree: Tree, tmp_path: Path) -> None:
  _ = tree.project.write_text(PROJECT)
  static = tmp_path / "dist"
  static.mkdir()
  web = TestClient(create_app(load_site(tree.site), static))

  response = web.get(f"{API}/runs")

  assert response.status_code == 404
  assert "names no monitor database" in response.json()["reason"]


@pytest.mark.parametrize(
  "query", ["runs", "window?run=1", "curve?run=1&t0=0&t1=1&attempt=a"]
)
def test_a_missing_database_is_a_state_of_its_own(
  web: TestClient, query: str
) -> None:
  response = web.get(f"{API}/{query}")

  assert response.status_code == 404
  assert response.json() == {
    "error": "no database",
    "reason": "no sampler has written the monitor database yet",
    "db": "../monitor.sqlite",
  }


def test_a_database_that_cannot_be_reached_is_no_missing_one(
  tree: Tree, tmp_path: Path
) -> None:
  # The path runs through a regular file: lstat fails with ENOTDIR, which
  # says nothing of whether a sampler has written the database.
  _ = (tree.root.parent / "file").write_text("")
  _ = tree.project.write_text(
    PROJECT + "monitor: {db: ../file/monitor.sqlite}\n"
  )
  static = tmp_path / "dist"
  static.mkdir()
  web = TestClient(create_app(load_site(tree.site), static))

  response = web.get(f"{API}/runs")

  assert response.status_code == 503
  assert "db" not in response.json()


@pytest.mark.parametrize("name", ["monitor.sqlite", "monitor.sqlite-shm"])
def test_a_symlinked_database_or_shm_is_never_opened(
  db: Path, web: TestClient, name: str
) -> None:
  target = db.parent / "elsewhere"
  _ = (
    (db.parent / name).replace(target) if (db.parent / name).exists() else None
  )
  if not target.exists():
    _ = target.write_bytes(b"")
  os.symlink(target, db.parent / name)
  before = target.read_bytes()

  response = web.get(f"{API}/runs")

  assert response.status_code == 503
  assert f"{name} is a symlink" in response.json()["reason"]
  assert target.read_bytes() == before


def test_the_monitor_routes_write_only_sqlite_s_own_files(
  db: Path, web: TestClient
) -> None:
  directory = db.parent
  for extra in (f"{db}-wal", f"{db}-shm"):
    Path(extra).unlink(missing_ok=True)
  before = files(directory)

  window = cast(dict[str, object], web.get(f"{API}/window?run=2").json())
  bar = cast(list[dict[str, str]], window["bars"])[0]
  for url in (
    f"{API}/runs",
    f"{API}/curve?run=2&t0=0&t1=2000000000&container={bar['container_id']}",
  ):
    assert web.get(url).status_code == 200, url
    for method in ("POST", "PUT", "PATCH", "DELETE"):
      assert web.request(method, url).status_code == 405, (method, url)

  after = files(directory)
  # A read-only reader of a WAL database makes its -wal and -shm when they
  # are missing; nothing else changes.
  assert set(after) - set(before) <= {
    "monitor.sqlite-wal",
    "monitor.sqlite-shm",
  }
  assert {k: after[k] for k in before} == before

from collections.abc import Callable, Generator
from contextlib import closing
from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from typing import cast, Literal

import pytest

from dataforge import cli
from dataforge.queue import (
  cancel,
  Config,
  ConfigError,
  count,
  DuplicateError,
  Job,
  jobs,
  load_config,
  submit,
)
from dataforge.queue.proc import alive, group_alive, Leader, leader
from dataforge.queue.scheduler import Scheduler
from dataforge.queue.wrapper import main as wrapper

QUEUE_FILE = """
queues: {alpha: 1, beta: 2}
max_running: 2
min_mem_available_mib: 100
max_load1: 4
database: queue.sqlite
logs: logs
cancel_grace_s: 0.5
poll_s: 0.1
"""


@dataclass
class Readings:
  mem: int = 100_000
  load: float = 0.0
  rss: dict[int, int] = field(default_factory=dict[int, int])

  def mem_available_mib(self) -> int:
    return self.mem

  def load1(self) -> float:
    return self.load

  def rss_mib(self, session: int) -> int:
    return self.rss.get(session, 0)


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
  monkeypatch.setenv("DATAFORGE_STATE_DIR", str(tmp_path / "state"))
  path = tmp_path / "queue.yaml"
  _ = path.write_text(QUEUE_FILE)
  return load_config(path)


@pytest.fixture
def readings() -> Readings:
  return Readings()


@pytest.fixture
def scheduler(config: Config, readings: Readings) -> Generator[Scheduler]:
  scheduler = Scheduler(config, readings)
  yield scheduler
  # Start nothing more, kill what a test left running, and wait for the
  # wrappers.
  if not config.database.exists():
    return
  for job in jobs(config, states={"queued"}):
    _ = cancel(config, job.id)
  for job in jobs(config, states={"running"}):
    started = job.leader()
    if started is not None and group_alive(started):
      os.killpg(started.pid, signal.SIGKILL)
  until(lambda: not jobs(config, states={"running"}), scheduler)
  for child in scheduler._children:  # pyright: ignore[reportPrivateUsage]
    _ = child.wait(timeout=20)


def until(
  done: Callable[[], bool], scheduler: Scheduler, timeout: float = 20
) -> None:
  deadline = time.monotonic() + timeout
  while True:
    scheduler.tick()
    if done():
      return
    assert time.monotonic() < deadline, "timed out"
    time.sleep(0.05)


def put(config: Config, queue: str, *argv: str, memory_mib: int = 10) -> Job:
  return submit(config, queue, argv, cwd=Path("/"), memory_mib=memory_mib)


def state(config: Config, job: Job) -> Job:
  (now,) = [j for j in jobs(config) if j.id == job.id]
  return now


def ended(config: Config, *wanted: Job) -> Callable[[], bool]:
  ids = {job.id for job in wanted}
  return lambda: all(
    job.ended_at is not None for job in jobs(config) if job.id in ids
  )


def ids(config: Config, status: str) -> list[int]:
  return sorted(job.id for job in jobs(config, states={status}))


def test_a_job_runs_and_its_end_is_recorded(
  config: Config, scheduler: Scheduler, tmp_path: Path
):
  ok = submit(
    config,
    "beta",
    ["sh", "-c", "pwd; echo out"],
    cwd=tmp_path,
    memory_mib=10,
    labels={"action": "build"},
  )
  bad = put(config, "beta", "sh", "-c", "exit 3")
  until(ended(config, ok, bad), scheduler)
  ok, bad = state(config, ok), state(config, bad)
  assert (ok.state, ok.exit_code) == ("done", 0)
  assert (bad.state, bad.exit_code) == ("failed", 3)
  assert ok.labels == {"action": "build"}
  assert ok.pid is not None and ok.starttime is not None
  assert ok.queued_at <= cast_float(ok.started_at) <= cast_float(ok.ended_at)
  assert ok.log == str(tmp_path / "logs" / f"{ok.id}.log")
  assert f"{tmp_path}\nout\n" in Path(ok.log).read_text()
  assert Path(ok.log).stat().st_mode & 0o777 == 0o600
  assert (tmp_path / "logs").stat().st_mode & 0o777 == 0o700


def cast_float(value: float | None) -> float:
  assert value is not None
  return value


def test_the_queue_and_the_machine_caps_hold(
  config: Config, scheduler: Scheduler
):
  a1, a2, b1, b2 = (
    put(config, queue, "sleep", "30")
    for queue in ("alpha", "alpha", "beta", "beta")
  )
  scheduler.tick()
  # alpha holds 1 and the machine 2: b2 waits though beta holds 2.
  assert ids(config, "running") == [a1.id, b1.id]
  assert ids(config, "queued") == [a2.id, b2.id]
  _ = cancel(config, a1.id)
  until(lambda: state(config, a2).state == "running", scheduler)
  assert ids(config, "running") == [a2.id, b1.id]


def test_a_queue_whose_oldest_job_does_not_fit_starts_none(
  config: Config, scheduler: Scheduler, readings: Readings
):
  readings.mem = 1000
  big = put(config, "beta", "sleep", "30", memory_mib=901)
  small = put(config, "beta", "sleep", "30", memory_mib=10)
  scheduler.tick()
  assert ids(config, "running") == []
  readings.mem = 1001
  scheduler.tick()
  # 1001 - 901 leaves the 100 MiB the file asks for; then nothing is left.
  assert ids(config, "running") == [big.id]
  assert state(config, small).state == "queued"


def test_a_running_job_holds_the_memory_it_has_not_grown_to(
  config: Config, scheduler: Scheduler, readings: Readings
):
  readings.mem = 1000
  first = put(config, "beta", "sleep", "30", memory_mib=600)
  second = put(config, "beta", "sleep", "30", memory_mib=400)
  scheduler.tick()
  scheduler.tick()
  assert ids(config, "running") == [first.id]
  pid = state(config, first).pid
  assert pid is not None
  # Grown to 500 of its 600: 100 is held, and 1000 - 100 - 400 leaves 500.
  readings.rss[pid] = 500
  scheduler.tick()
  assert ids(config, "running") == [first.id, second.id]


def test_a_load_above_max_load1_starts_nothing(
  config: Config, scheduler: Scheduler, readings: Readings
):
  readings.load = 4.01
  job = put(config, "beta", "sleep", "30")
  scheduler.tick()
  assert state(config, job).state == "queued"
  readings.load = 4.0
  scheduler.tick()
  assert state(config, job).state == "running"


def test_a_key_is_refused_while_its_job_is_queued_or_running(
  config: Config, scheduler: Scheduler
):
  def keyed(queue: str) -> Job:
    return submit(
      config, queue, ["sleep", "30"], cwd=Path("/"), memory_mib=1, key="k"
    )

  first = keyed("alpha")
  with pytest.raises(DuplicateError) as queued:
    _ = keyed("beta")
  assert queued.value.job.id == first.id
  scheduler.tick()
  with pytest.raises(DuplicateError) as running:
    _ = keyed("alpha")
  assert (running.value.job.id, running.value.job.state) == (
    first.id,
    "running",
  )
  _ = cancel(config, first.id)
  until(ended(config, first), scheduler)
  assert keyed("alpha").state == "queued"


def test_canceling_a_queued_job_ends_it_at_once(config: Config):
  job = put(config, "alpha", "sleep", "30")
  done = cancel(config, job.id)
  assert (done.state, done.pid, done.exit_code) == ("canceled", None, None)
  assert done.ended_at is not None and done.cancel_requested_at is not None


def test_canceling_a_running_job_sends_its_group_sigterm(
  config: Config, scheduler: Scheduler
):
  job = put(config, "alpha", "sh", "-c", "echo ready; exec sleep 30")
  until(lambda: "ready" in log(config, job), scheduler)
  asked = cancel(config, job.id)
  assert asked.state == "running" and asked.cancel_requested_at is not None
  until(ended(config, job), scheduler)
  done = state(config, job)
  assert (done.state, done.exit_code) == ("canceled", -signal.SIGTERM)


def test_a_job_that_outlasts_the_grace_period_gets_sigkill(
  config: Config, scheduler: Scheduler
):
  job = put(config, "alpha", "sh", "-c", "trap '' TERM; echo ready; sleep 30")
  until(lambda: "ready" in log(config, job), scheduler)
  _ = cancel(config, job.id)
  scheduler.tick()
  assert state(config, job).state == "running"
  until(ended(config, job), scheduler)
  done = state(config, job)
  # The SIGKILL took the wrapper too: the scheduler wrote the end.
  assert (done.state, done.exit_code) == ("canceled", None)
  assert cast_float(done.ended_at) - cast_float(done.cancel_requested_at) >= 0.5


def log(config: Config, job: Job) -> str:
  path = Path(state(config, job).log)
  return path.read_text() if path.exists() else ""


def test_a_restarted_scheduler_takes_the_running_jobs_as_they_are(
  config: Config, scheduler: Scheduler, tmp_path: Path
):
  job = put(config, "alpha", "sh", "-c", "echo ready; sleep 2; exit 4")
  serving = subprocess.Popen(
    [
      Path(sys.executable).parent / "dataforge",
      "queue",
      "serve",
      "--config",
      str(tmp_path / "queue.yaml"),
    ],
    stderr=subprocess.DEVNULL,
  )
  try:
    deadline = time.monotonic() + 20
    while "ready" not in log(config, job):
      assert time.monotonic() < deadline, "timed out"
      time.sleep(0.05)
  finally:
    serving.terminate()
    assert serving.wait(timeout=20) == 0
  running = state(config, job)
  assert running.state == "running"
  started = running.leader()
  assert started is not None and alive(started)
  until(ended(config, job), scheduler)
  # The exit status reached the table, though no scheduler waited for it.
  done = state(config, job)
  assert (done.state, done.exit_code) == ("failed", 4)


def test_a_restarted_scheduler_counts_the_running_jobs_in_its_caps(
  config: Config, scheduler: Scheduler, tmp_path: Path
):
  def waiting(name: str) -> str:
    go = tmp_path / name
    return f"echo ready; while [ ! -e {go} ]; do sleep 0.05; done"

  a = put(config, "alpha", "sh", "-c", waiting("go-a"))
  b = put(config, "beta", "sh", "-c", waiting("go-b"))
  # The first scheduler reads the machine's own load; a busy machine must
  # not keep it from starting a and b.
  first = tmp_path / "first.yaml"
  _ = first.write_text(QUEUE_FILE.replace("max_load1: 4", "max_load1: 10000"))
  serving = subprocess.Popen(
    [
      Path(sys.executable).parent / "dataforge",
      "queue",
      "serve",
      "--config",
      str(first),
    ],
    stderr=subprocess.DEVNULL,
  )
  try:
    deadline = time.monotonic() + 20
    while not ("ready" in log(config, a) and "ready" in log(config, b)):
      assert time.monotonic() < deadline, "timed out"
      time.sleep(0.05)
  finally:
    serving.terminate()
    assert serving.wait(timeout=20) == 0
  c, d = put(config, "alpha", "true"), put(config, "beta", "true")
  # a and b reach max_running: nothing starts.
  for _ in range(5):
    scheduler.tick()
  assert ids(config, "running") == [a.id, b.id]
  assert ids(config, "queued") == [c.id, d.id]
  # b ends; a still holds alpha's cap of 1, so only d starts.
  (tmp_path / "go-b").touch()
  until(ended(config, b, d), scheduler)
  for _ in range(5):
    scheduler.tick()
  assert ids(config, "running") == [a.id]
  assert ids(config, "queued") == [c.id]
  (tmp_path / "go-a").touch()
  until(ended(config, a, c), scheduler)
  for job in (a, b, c, d):
    done = state(config, job)
    assert (done.state, done.exit_code) == ("done", 0)
  assert cast_float(state(config, a).ended_at) <= cast_float(
    state(config, c).started_at
  )


def test_a_job_runs_in_its_directory_as_its_claim_resolved_it(
  config: Config,
  scheduler: Scheduler,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
):
  first, second = tmp_path / "releases" / "a", tmp_path / "releases" / "b"
  first.mkdir(parents=True)
  second.mkdir()
  current = tmp_path / "current"
  current.symlink_to(first)
  start = Scheduler._start  # pyright: ignore[reportPrivateUsage]

  def start_then_switch(self: Scheduler, conn: sqlite3.Connection, job: Job):
    start(self, conn, job)
    # The wrapper waits for the claim's commit before it runs the command:
    # the link moves on in between.
    switched = tmp_path / "switched"
    switched.symlink_to(second)
    _ = switched.replace(current)

  monkeypatch.setattr(Scheduler, "_start", start_then_switch)
  job = submit(
    config,
    "alpha",
    ["sh", "-c", "pwd -P; touch here"],
    cwd=current,
    memory_mib=10,
  )
  assert state(config, job).cwd == str(current)
  until(ended(config, job), scheduler)
  done = state(config, job)
  assert (done.state, done.exit_code) == ("done", 0)
  assert done.cwd == str(first)
  assert current.resolve() == second
  assert f"{first}\n" in log(config, job)
  assert (first / "here").exists() and not (second / "here").exists()


def test_a_job_whose_wrapper_is_gone_is_recorded_failed(
  config: Config, scheduler: Scheduler
):
  job = put(config, "alpha", "sleep", "30")
  scheduler.tick()
  pid = state(config, job).pid
  assert pid is not None
  os.killpg(pid, signal.SIGKILL)
  until(ended(config, job), scheduler)
  done = state(config, job)
  assert (done.state, done.exit_code) == ("failed", None)


def test_a_pid_of_another_start_or_boot_is_not_the_job():
  me = leader(os.getpid())
  assert me is not None
  assert alive(me)
  assert not alive(me._replace(start=me.start + 1))
  assert not alive(me._replace(boot="another boot"))
  assert not group_alive(me._replace(boot="another boot"))


def test_a_job_whose_wrapper_alone_is_killed_ends_with_its_group(
  config: Config, scheduler: Scheduler
):
  job = put(config, "alpha", "sh", "-c", "echo ready; exec sleep 30")
  until(lambda: "ready" in log(config, job), scheduler)
  started = state(config, job).leader()
  assert started is not None
  os.kill(started.pid, signal.SIGKILL)
  until(ended(config, job), scheduler)
  done = state(config, job)
  assert (done.state, done.exit_code) == ("failed", None)
  # The command ended before its row did.
  assert not group_alive(started)
  assert not group_alive(Leader(started.pid, 0, started.boot))


def test_the_wrapper_runs_nothing_for_a_job_not_claimed_for_it(
  config: Config, tmp_path: Path
):
  marker = tmp_path / "ran"
  job = put(config, "alpha", "touch", str(marker))
  assert wrapper([str(config.database), str(job.id)]) == 1
  assert not marker.exists()
  assert state(config, job).state == "queued"


def test_list_json_gives_the_table_rows(
  config: Config,
  scheduler: Scheduler,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
):
  first = put(config, "alpha", "true")
  _ = submit(
    config,
    "beta",
    ["sleep", "30"],
    cwd=Path("/"),
    memory_mib=5,
    key="k",
    labels={"item": "x"},
  )
  until(ended(config, first), scheduler)
  out = io.StringIO()
  monkeypatch.setattr(sys, "stdout", out)
  path = str(tmp_path / "queue.yaml")
  assert cli.main(["queue", "list", "--config", path, "--json"]) == 0
  listed = cast(list[dict[str, object]], json.loads(out.getvalue()))
  with closing(sqlite3.connect(config.database)) as conn:
    conn.row_factory = sqlite3.Row
    found = cast(
      list[sqlite3.Row],
      conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall(),
    )
  rows = [cast(dict[str, object], dict(r)) for r in found]
  for row in rows:
    for column in ("argv", "labels"):
      row[column] = cast(object, json.loads(cast(str, row[column])))
  assert listed == rows
  assert [r["state"] for r in listed] == ["running", "done"]


def test_jobs_reads_without_making_the_database(
  config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  with pytest.raises(FileNotFoundError):
    _ = jobs(config)
  out = io.StringIO()
  monkeypatch.setattr(sys, "stdout", out)
  path = str(tmp_path / "queue.yaml")
  assert cli.main(["queue", "list", "--config", path, "--json"]) == 0
  assert json.loads(out.getvalue()) == []
  assert not config.database.exists()


def test_the_database_and_the_logs_default_to_the_state_directory(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setenv("DATAFORGE_STATE_DIR", str(tmp_path / "state"))
  path = tmp_path / "queue.yaml"
  _ = path.write_text(
    "queues: {alpha: 1}\nmax_running: 1\nmin_mem_available_mib: 0\n"
    + "max_load1: 1\n"
  )
  config = load_config(path)
  assert config.database == tmp_path / "state" / "queue" / "queue.sqlite"
  assert config.logs == tmp_path / "state" / "queue" / "logs"


@pytest.mark.parametrize(
  "line",
  ["max_running: 0", "queues: {}", "max_load1: -1", "colour: blue"],
)
def test_a_queue_file_that_fails_a_check_is_refused(tmp_path: Path, line: str):
  path = tmp_path / "queue.yaml"
  lines = [
    "queues: {alpha: 1}",
    "max_running: 1",
    "min_mem_available_mib: 0",
    "max_load1: 1",
  ]
  key = line.split(":")[0]
  kept = [kept for kept in lines if not kept.startswith(f"{key}:")]
  _ = path.write_text("\n".join([*kept, line]) + "\n")
  with pytest.raises(ConfigError, match=key):
    _ = load_config(path)


def test_jobs_by_when_they_ended_and_their_count(config: Config):
  first, second, third = (put(config, "alpha", "true") for _ in range(3))
  open_job = put(config, "beta", "true")
  # They end in another order than they were queued in.
  for job in (third, first, second):
    _ = cancel(config, job.id)
    time.sleep(0.01)

  ended = jobs(config, states={"canceled"}, by="ended_at")
  last = jobs(config, by="ended_at", limit=2)
  every = jobs(config, by="ended_at")

  assert [j.id for j in ended] == [second.id, first.id, third.id]
  assert [j.id for j in last] == [second.id, first.id]
  # A job that has not ended comes last.
  assert every[-1].id == open_job.id
  assert count(config) == 4
  assert count(config, states={"canceled"}) == 3
  assert count(config, queue="beta", states={"queued"}) == 1
  with pytest.raises(ValueError, match="unknown order"):
    _ = jobs(config, by=cast(Literal["id"], cast(object, "queued_at")))


def test_count_reads_without_making_the_database(config: Config):
  with pytest.raises(FileNotFoundError):
    _ = count(config)
  assert not config.database.exists()

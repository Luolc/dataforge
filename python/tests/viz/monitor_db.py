"""A synthetic resource monitor database, for the tests and the screenshots.

Its runs, machine, containers and attempts were made up and ran nowhere.
Write one for the frontend's fixtures from python/:

  uv run python -m tests.viz.monitor_db ../web/fixtures/monitor/monitor.sqlite
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import random
import sqlite3
from typing import cast

SCHEMA = """
CREATE TABLE runs (
  run_id INTEGER PRIMARY KEY, version INTEGER NOT NULL,
  started_at REAL NOT NULL, ended_at REAL, stop_reason TEXT,
  interval_s REAL NOT NULL, host TEXT NOT NULL, cpus INTEGER NOT NULL,
  mem_total_bytes INTEGER NOT NULL, source TEXT NOT NULL, pid INTEGER
);
CREATE TABLE machine (
  run_id INTEGER NOT NULL, sample INTEGER NOT NULL, t REAL NOT NULL,
  version INTEGER NOT NULL, cpu_cores REAL, iowait_cores REAL,
  cpus INTEGER NOT NULL, load1 REAL NOT NULL, load5 REAL NOT NULL,
  load15 REAL NOT NULL, mem_total_bytes INTEGER NOT NULL,
  mem_available_bytes INTEGER NOT NULL, swap_total_bytes INTEGER NOT NULL,
  swap_used_bytes INTEGER NOT NULL, sampler_cpu_s REAL NOT NULL
);
CREATE TABLE container (
  run_id INTEGER NOT NULL, sample INTEGER NOT NULL, t REAL NOT NULL,
  version INTEGER NOT NULL, container_id TEXT NOT NULL, name TEXT NOT NULL,
  image TEXT NOT NULL, run TEXT NOT NULL, item TEXT, attempt TEXT,
  state TEXT, visit INTEGER, trial TEXT, cpu_cores REAL, mem_bytes INTEGER,
  mem_limit_bytes INTEGER, cpu_limit_cores REAL
);
CREATE TABLE batch (
  run_id INTEGER NOT NULL, sample INTEGER NOT NULL, t REAL NOT NULL,
  version INTEGER NOT NULL, attempts INTEGER, sessions INTEGER NOT NULL,
  paused INTEGER, pool_bytes INTEGER, pool_reserved_bytes INTEGER,
  pool_waiting INTEGER
);
CREATE TABLE events (
  run_id INTEGER NOT NULL, sample INTEGER NOT NULL, t REAL NOT NULL,
  version INTEGER NOT NULL, kind TEXT NOT NULL, container_id TEXT,
  name TEXT, image TEXT, run TEXT, item TEXT, attempt TEXT, state TEXT,
  visit INTEGER, trial TEXT, start_from TEXT, exit_code INTEGER,
  reason TEXT, delay_s REAL, until REAL, wait_id INTEGER, mem_bytes INTEGER
);
CREATE INDEX machine_run_t ON machine (run_id, t);
CREATE INDEX container_run_t ON container (run_id, t);
CREATE INDEX container_run_attempt_t ON container (run_id, attempt, t);
CREATE INDEX container_run_id_t ON container (run_id, container_id, t);
CREATE INDEX batch_run_t ON batch (run_id, t);
CREATE INDEX events_run_kind_t ON events (run_id, kind, t);
"""

START = 1_790_000_000.0
"""The first run's start, in seconds since the epoch."""

INTERVAL = 10.0
GIB = 1 << 30
CPUS = 32
MEM = 128 * GIB


@dataclass
class Container:
  """A made-up container: where it belongs, when it ran and how hard.

  Attributes:
    id: Its 64-digit ID.
    image: Its image.
    item: Its item, or None.
    attempt: Its attempt's ID, or None.
    state: The state of its trial, or None.
    visit: That state's visit, or None.
    trial: Its trial, or None.
    start: When it started.
    end: When it ended; None when it ran on past the sampler.
    exit_code: Its exit code.
    cpu: The cores it uses about.
    mem: The bytes it uses about.
  """

  id: str
  image: str
  item: str | None
  attempt: str | None
  state: str | None
  visit: int | None
  trial: str | None
  start: float
  end: float | None
  exit_code: int
  cpu: float
  mem: int

  def labels(self) -> tuple[object, ...]:
    """Return its columns from container_id to trial, as a row has them.

    Returns:
      The columns' values, in the table's order.
    """
    run = "sample-" + (self.attempt or self.item or "other").replace("/", "-")
    if self.state is not None:
      run += f"-{self.state}-{self.visit}-{self.trial}"
    name = f"box-{self.id[:8]}"
    return (
      self.id,
      name,
      self.image,
      run,
      self.item,
      self.attempt,
      self.state,
      self.visit,
      self.trial,
    )


def attempt_ids(count: int) -> list[str]:
  """Return count attempt IDs, the first six those of web/fixtures/attempts.

  Args:
    count: How many.

  Returns:
    The IDs, as <batch>/<task>.
  """
  ids = [f"batch-{1 + i // 3}/task-{i + 1:04d}" for i in range(count)]
  return ids


def containers(rng: random.Random, attempts: list[str], t0: float):
  """Make each attempt's containers, its states one after another.

  Args:
    rng: The random numbers.
    attempts: The attempt IDs.
    t0: When the first attempt starts.

  Returns:
    The containers, and when the last one ends.
  """
  made: list[Container] = []

  def add(**kw: object) -> Container:
    c = Container(id=f"{rng.getrandbits(256):064x}", **kw)  # pyright: ignore[reportArgumentType]
    made.append(c)
    return c

  latest = t0
  for i, attempt in enumerate(attempts):
    item = attempt.split("/")[0]
    start = t0 + i * 420 + rng.uniform(0, 120)
    t = start + 20
    agent = f"sample-agent:{attempt.replace('/', '-')}"
    task = f"sample-task:{attempt.replace('/', '-')}"
    # The session's container lives through every state.
    session = add(
      image=agent,
      item=item,
      attempt=attempt,
      state=None,
      visit=None,
      trial=None,
      start=start,
      end=None,
      exit_code=0,
      cpu=0.6,
      mem=3 * GIB,
    )
    t += rng.uniform(300, 600)  # author
    for trial in ("nop-1", "oracle-1"):
      length = rng.uniform(60, 180)
      for image in (task, "sample-sidecar:1"):
        _ = add(
          image=image,
          item=item,
          attempt=attempt,
          state="check",
          visit=1,
          trial=trial,
          start=t + rng.uniform(0, 3),
          end=t + length,
          exit_code=0,
          cpu=1.4 if image == task else 0.1,
          mem=(2 if image == task else 0) * GIB + (300 << 20),
        )
      t += length + 5
    solve = t
    longest = 0.0
    for k in range(4):
      length = rng.uniform(300, 1100)
      oom = i % 4 == 1 and k == 2
      longest = max(longest, length)
      for image in (task, "sample-sidecar:1"):
        _ = add(
          image=image,
          item=item,
          attempt=attempt,
          state="solve",
          visit=1,
          trial=f"trial-{k}",
          start=solve + rng.uniform(0, 4),
          end=solve + length,
          exit_code=137 if oom and image == task else 0,
          cpu=1.8 if image == task else 0.1,
          mem=(4 if image == task else 0) * GIB + (200 << 20),
        )
      # A trial's short-lived eval container.
      _ = add(
        image=task,
        item=item,
        attempt=attempt,
        state="solve",
        visit=1,
        trial=f"trial-{k}",
        start=solve + length - 25,
        end=solve + length - 3,
        exit_code=0 if k != 3 else 1,
        cpu=1.0,
        mem=GIB,
      )
    t = solve + longest + 10
    _ = add(
      image=agent,
      item=item,
      attempt=attempt,
      state="review",
      visit=1,
      trial=None,
      start=t,
      end=t + rng.uniform(120, 300),
      exit_code=0,
      cpu=0.4,
      mem=GIB,
    )
    t = made[-1].end or t
    session.end = t + 5
    # The container that gives an item's files back: its item only.
    _ = add(
      image="sample-tools:1",
      item=item,
      attempt=None,
      state=None,
      visit=None,
      trial=None,
      start=t + 8,
      end=t + 9.5,
      exit_code=0,
      cpu=0.0,
      mem=0,
    )
    latest = max(latest, t + 10)
  return made, latest


def make_db(path: Path, attempts: int = 6, seed: int = 7) -> None:
  """Write a database of two runs: a short standalone one, then a batch's.

  Args:
    path: Where; it must not exist.
    attempts: How many attempts the batch runs.
    seed: The random numbers' seed.
  """
  rng = random.Random(seed)
  db = sqlite3.connect(path)
  try:
    _ = db.execute("PRAGMA journal_mode=WAL")
    _ = db.execute("PRAGMA user_version=2")
    _ = db.executescript(SCHEMA)
    _run(db, rng, 1, START, [], "standalone", 1200.0, "pid_exited")
    t0 = START + 3600
    made, end = containers(rng, attempt_ids(attempts), t0 + 60)
    # The last trial still runs when the sampler stops: no container_end.
    made[-3].end = None
    _run(db, rng, 2, t0, made, "batch", end + 60 - t0, "ended")
    db.commit()
  finally:
    db.close()


def _run(
  db: sqlite3.Connection,
  rng: random.Random,
  run_id: int,
  t0: float,
  made: list[Container],
  source: str,
  length: float,
  reason: str,
) -> None:
  end = t0 + length
  _ = db.execute(
    "INSERT INTO runs VALUES (?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (
      run_id,
      t0,
      end,
      reason,
      INTERVAL,
      "0123456789ab",
      CPUS,
      MEM,
      source,
      4242 if source == "batch" else None,
    ),
  )
  pauses = [(t0 + length * 0.35, 240.0), (t0 + length * 0.62, 90.0)]
  for start, delay in pauses if source == "batch" else []:
    _event(db, run_id, start, "pause_start", reason="429", delay_s=delay)
    _event(db, run_id, start + delay, "pause_end")
  for c in made:
    _event(db, run_id, c.start, "container_start", c, start_from="event")
    if c.end is not None:
      _event(db, run_id, c.end, "container_end", c, exit_code=c.exit_code)
  sample = 0
  t = t0
  while t < end:
    running = [c for c in made if c.start <= t and (c.end or end) > t]
    cpu = 0.0
    mem = 0
    for c in running:
      c_cpu = max(0.0, c.cpu * rng.uniform(0.5, 1.3))
      c_mem = int(c.mem * rng.uniform(0.8, 1.05))
      cpu += c_cpu
      mem += c_mem
      _ = db.execute(
        "INSERT INTO container VALUES"
        + " (?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
          run_id,
          sample,
          t,
          *c.labels(),
          c_cpu,
          c_mem,
          8 * GIB if c.state else None,
          2.0 if c.state else None,
        ),
      )
    busy = cpu + rng.uniform(0.5, 1.5)
    used = 9 * GIB + mem
    _ = db.execute(
      "INSERT INTO machine VALUES"
      + " (?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      (
        run_id,
        sample,
        t,
        None if sample == 0 else busy,
        rng.uniform(0, 0.3),
        CPUS,
        busy * 1.1,
        busy,
        busy * 0.9,
        MEM,
        MEM - used,
        8 * GIB,
        int(max(0, used - 100 * GIB) * 0.1),
        0.004,
      ),
    )
    if source == "batch":
      attempts = {c.attempt for c in running if c.attempt}
      paused = any(s <= t < s + d for s, d in pauses)
      reserved = sum(8 * GIB for c in running if c.state)
      _ = db.execute(
        "INSERT INTO batch VALUES (?, ?, ?, 2, ?, ?, ?, ?, ?, ?)",
        (
          run_id,
          sample,
          t,
          len(attempts),
          len({c.attempt for c in running if c.state != "solve"}),
          int(paused),
          96 * GIB,
          min(reserved, 96 * GIB),
          max(0, (reserved - 96 * GIB) // (8 * GIB)),
        ),
      )
    sample += 1
    t += INTERVAL


def _event(
  db: sqlite3.Connection,
  run_id: int,
  t: float,
  kind: str,
  c: Container | None = None,
  **columns: object,
) -> None:
  labels = c.labels() if c else (None,) * 9
  _ = db.execute(
    "INSERT INTO events (run_id, sample, t, version, kind, container_id,"
    + " name, image, run, item, attempt, state, visit, trial, start_from,"
    + " exit_code, reason, delay_s, until)"
    + " VALUES (?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (
      run_id,
      0,
      t,
      kind,
      *labels,
      columns.get("start_from"),
      columns.get("exit_code"),
      columns.get("reason"),
      columns.get("delay_s"),
      None if "delay_s" not in columns else t + cast_float(columns["delay_s"]),
    ),
  )


def cast_float(value: object) -> float:
  """Return value as a float; it is one.

  Args:
    value: A number.

  Returns:
    The number.
  """
  assert isinstance(value, float)
  return value


def main() -> None:
  """Write the database the command line names."""
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("path", type=Path)
  _ = parser.add_argument("--attempts", type=int, default=6)
  args = parser.parse_args()
  path = cast(Path, args.path)
  path.unlink(missing_ok=True)
  make_db(path, cast(int, args.attempts))


if __name__ == "__main__":
  main()

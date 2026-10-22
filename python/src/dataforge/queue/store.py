"""The job table, in one SQLite file in WAL mode, and its reads and writes.

A job goes queued -> running -> done, failed or canceled; an ended row stays.
A queue may be paused: a row of the table pauses, kept until it is resumed,
from which the scheduler starts no job.
Every write runs in a BEGIN IMMEDIATE transaction, so that a claim, a
submission and a cancellation see the table as no other write leaves it
half-done. Each call opens its own connection and closes it.
"""

from collections.abc import Collection, Generator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import signal
import sqlite3
import time
from typing import cast, Literal

from dataforge.queue.config import Config
from dataforge.queue.proc import Leader, signal_group

STATES = ("queued", "running", "done", "failed", "canceled")
"""The states a job may be in."""

LIVE = ("queued", "running")
"""The states in which a job holds its dedup key."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  queue TEXT NOT NULL,
  argv TEXT NOT NULL,
  cwd TEXT NOT NULL,
  memory_mib INTEGER NOT NULL,
  dedup_key TEXT,
  labels TEXT NOT NULL,
  state TEXT NOT NULL
    CHECK (state IN ('queued', 'running', 'done', 'failed', 'canceled')),
  pid INTEGER,
  starttime INTEGER,
  boot_id TEXT,
  exit_code INTEGER,
  queued_at REAL NOT NULL,
  started_at REAL,
  ended_at REAL,
  cancel_requested_at REAL,
  log TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS jobs_live_key ON jobs (dedup_key)
  WHERE state IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS jobs_state ON jobs (state, queue, id);
CREATE TABLE IF NOT EXISTS pauses (
  queue TEXT PRIMARY KEY,
  paused_at REAL NOT NULL,
  reason TEXT
) STRICT;
"""

_INSERT = """
INSERT INTO jobs (queue, argv, cwd, memory_mib, dedup_key, labels, state,
  queued_at, log)
VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, '')
"""

_CANCEL_QUEUED = """
UPDATE jobs SET state = 'canceled', ended_at = ?, cancel_requested_at = ?
WHERE id = ? AND state = 'queued'
"""

_CANCEL_RUNNING = """
UPDATE jobs SET cancel_requested_at = ?
WHERE id = ? AND state = 'running' AND cancel_requested_at IS NULL
"""

_PAUSE = """
INSERT INTO pauses (queue, paused_at, reason) VALUES (?, ?, ?)
ON CONFLICT (queue) DO NOTHING
"""

_BUSY_MS = 30_000


class DuplicateError(Exception):
  """A submission whose dedup key a queued or running job holds.

  Attributes:
    job: The job that holds it.
  """

  job: Job

  def __init__(self, job: Job) -> None:
    """Make the error.

    Args:
      job: The job that holds the key.
    """
    super().__init__(
      f"job {job.id} ({job.state}) already holds the key {job.dedup_key}"
    )
    self.job = job


class NoSuchJobError(LookupError):
  """A job ID that is not in the table."""


@dataclass(frozen=True, slots=True)
class Job:
  """A row of the job table.

  Attributes:
    id: Its ID, in the order of submission.
    queue: The queue it is in.
    argv: The command, run without a shell.
    cwd: The directory it runs in.
    memory_mib: The memory it declares, in MiB.
    dedup_key: The key no other queued or running job may hold, or None.
    labels: Opaque labels for whoever shows it.
    state: One of STATES.
    pid: Its wrapper's pid, the leader of its session; None until it starts.
    starttime: The pid's starttime in /proc/<pid>/stat.
    boot_id: The ID of the boot it started in.
    exit_code: The command's exit status, negative for the signal that ended
      it; None when none was collected.
    queued_at: When it was submitted, in seconds since the epoch.
    started_at: When it started.
    ended_at: When it ended.
    cancel_requested_at: When its cancellation was first asked for.
    log: The file its output goes to.
  """

  id: int
  queue: str
  argv: tuple[str, ...]
  cwd: str
  memory_mib: int
  dedup_key: str | None
  labels: dict[str, str]
  state: str
  pid: int | None
  starttime: int | None
  boot_id: str | None
  exit_code: int | None
  queued_at: float
  started_at: float | None
  ended_at: float | None
  cancel_requested_at: float | None
  log: str

  def to_json(self) -> dict[str, object]:
    """Give the row as JSON: its columns, argv a list, labels an object.

    Returns:
      The mapping.
    """
    row = asdict(self)
    row["argv"] = list(self.argv)
    return row

  def leader(self) -> Leader | None:
    """Give the wrapper that runs the job, as the scheduler recorded it.

    Returns:
      The leader; None before the job started.
    """
    if self.pid is None or self.starttime is None or self.boot_id is None:
      return None
    return Leader(self.pid, self.starttime, self.boot_id)


@dataclass(frozen=True, slots=True)
class Pause:
  """A row of the table pauses: a queue from which no job starts.

  Attributes:
    queue: The queue.
    paused_at: When it was paused, in seconds since the epoch.
    reason: Why, as whoever paused it said; None when they said nothing.
  """

  queue: str
  paused_at: float
  reason: str | None

  def to_json(self) -> dict[str, object]:
    """Give the row as JSON, its columns.

    Returns:
      The mapping.
    """
    return asdict(self)


@contextmanager
def connect(path: Path) -> Generator[sqlite3.Connection]:
  """Open the database, making it and its table when missing.

  Args:
    path: The SQLite file.

  Yields:
    A connection in autocommit mode; write() opens its transactions.
  """
  path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  with closing(
    sqlite3.connect(path, timeout=_BUSY_MS / 1000, autocommit=True)
  ) as conn:
    conn.row_factory = sqlite3.Row
    _ = conn.execute("PRAGMA journal_mode = WAL")
    _ = conn.executescript(_SCHEMA)
    yield conn


@contextmanager
def write(conn: sqlite3.Connection) -> Generator[None]:
  """Run a BEGIN IMMEDIATE transaction, committed when the block ends.

  Args:
    conn: The connection.

  Yields:
    Nothing; a raise rolls back.
  """
  _ = conn.execute("BEGIN IMMEDIATE")
  committed = False
  try:
    yield
    _ = conn.execute("COMMIT")
    committed = True
  finally:
    if not committed:
      _ = conn.execute("ROLLBACK")


def row(conn: sqlite3.Connection, job_id: int) -> Job | None:
  """Read one job.

  Args:
    conn: The connection.
    job_id: Its ID.

  Returns:
    The job, or None when there is none with that ID.
  """
  found = cast(
    sqlite3.Row | None,
    conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(),
  )
  return None if found is None else _job(found)


def select(
  conn: sqlite3.Connection, where: str, params: Sequence[object] = ()
) -> list[Job]:
  """Read the jobs a condition picks, by ID.

  Args:
    conn: The connection.
    where: The condition, with ? for each parameter.
    params: The parameters.

  Returns:
    The jobs, the oldest first.
  """
  rows = cast(
    list[sqlite3.Row],
    conn.execute(
      f"SELECT * FROM jobs WHERE {where} ORDER BY id", params
    ).fetchall(),
  )
  return [_job(found) for found in rows]


def submit(
  config: Config,
  queue: str,
  argv: Sequence[str],
  *,
  cwd: Path,
  memory_mib: int,
  key: str | None = None,
  labels: Mapping[str, str] | None = None,
) -> Job:
  """Put a job in a queue.

  Args:
    config: The queue file.
    queue: The queue, one the file names.
    argv: The command, run without a shell; not empty.
    cwd: The directory it runs in, an absolute path to a directory.
    memory_mib: The memory it declares, in MiB.
    key: The dedup key, or None for none.
    labels: Opaque labels for whoever shows the job.

  Returns:
    The job, queued.

  Raises:
    ValueError: When an argument is not one the queue file allows.
    DuplicateError: When a queued or running job holds the key.
  """  # noqa: DOC503
  argv = list(argv)
  labels = dict(labels or {})
  _check(config, queue, argv, cwd, memory_mib)
  with connect(config.database) as conn, write(conn):
    try:
      cursor = conn.execute(
        _INSERT,
        (
          queue,
          json.dumps(argv),
          str(cwd),
          memory_mib,
          key,
          json.dumps(labels),
          time.time(),
        ),
      )
    except sqlite3.IntegrityError:
      holders = select(
        conn, "dedup_key = ? AND state IN ('queued', 'running')", (key,)
      )
      if not holders:
        raise
      raise DuplicateError(holders[0]) from None
    job_id = cast(int, cursor.lastrowid)
    log = config.logs.resolve() / f"{job_id}.log"
    _ = conn.execute("UPDATE jobs SET log = ? WHERE id = ?", (str(log), job_id))
    return cast(Job, row(conn, job_id))


def jobs(
  config: Config,
  *,
  queue: str | None = None,
  states: Collection[str] | None = None,
  limit: int | None = None,
  by: Literal["id", "ended_at"] = "id",
) -> list[Job]:
  """List jobs, the newest first.

  It opens the database read-only and writes nothing.

  Args:
    config: The queue file.
    queue: Only this queue's jobs; None for every queue.
    states: Only jobs in these of STATES; None for any.
    limit: At most this many, the newest; None for all.
    by: What newest is by: "id", the order they were queued in, or
      "ended_at", when they ended, then by ID, a job that has not ended
      last.

  Returns:
    The jobs.

  Raises:
    ValueError: When a state is not one of STATES, or by is neither.
    FileNotFoundError: When the database does not exist yet.
  """  # noqa: DOC503
  # SQLite sorts NULL first, so a job not ended comes last in DESC.
  orders = {"id": "id DESC", "ended_at": "ended_at DESC, id DESC"}
  if by not in orders:
    raise ValueError(f"unknown order {by}")
  where, params = _where(queue, states)
  sql = f"SELECT * FROM jobs WHERE {where} ORDER BY {orders[by]}"
  if limit is not None:
    sql += " LIMIT ?"
    params.append(limit)
  return [_job(found) for found in _read(config, sql, params)]


def count(
  config: Config,
  *,
  queue: str | None = None,
  states: Collection[str] | None = None,
) -> int:
  """Count jobs, as jobs would list them with no limit.

  It opens the database read-only and writes nothing.

  Args:
    config: The queue file.
    queue: Only this queue's jobs; None for every queue.
    states: Only jobs in these of STATES; None for any.

  Returns:
    How many there are.

  Raises:
    ValueError: When a state is not one of STATES.
    FileNotFoundError: When the database does not exist yet.
  """  # noqa: DOC502
  where, params = _where(queue, states)
  [found] = _read(config, f"SELECT COUNT(*) FROM jobs WHERE {where}", params)
  return cast(int, found[0])


def _where(
  queue: str | None, states: Collection[str] | None
) -> tuple[str, list[object]]:
  where, params = ["1"], list[object]()
  if queue is not None:
    where.append("queue = ?")
    params.append(queue)
  if states is not None:
    if unknown := sorted(set(states) - set(STATES)):
      raise ValueError(f"unknown state {unknown[0]}")
    where.append(f"state IN ({', '.join('?' * len(states))})")
    params.extend(states)
  return " AND ".join(where), params


def _read(
  config: Config, sql: str, params: Sequence[object]
) -> list[sqlite3.Row]:
  # Read-only: no table is made and nothing is written.
  if not config.database.exists():
    raise FileNotFoundError(f"{config.database}: no such database")
  uri = f"{config.database.resolve().as_uri()}?mode=ro"
  with closing(sqlite3.connect(uri, uri=True, timeout=_BUSY_MS / 1000)) as conn:
    conn.row_factory = sqlite3.Row
    return cast(list[sqlite3.Row], conn.execute(sql, params).fetchall())


def cancel(config: Config, job_id: int) -> Job:
  """Cancel a job.

  A queued job becomes canceled at once. A running one is marked, and its
  process group gets SIGTERM; it is recorded canceled when it ends, and its
  wrapper, and the scheduler while it runs, send the group SIGKILL once
  cancel_grace_s has passed. Asking again sends SIGTERM again. An ended job
  is left as it is.

  Args:
    config: The queue file.
    job_id: The job.

  Returns:
    The job after the call.

  Raises:
    NoSuchJobError: When there is no such job.
  """
  now = time.time()
  with connect(config.database) as conn:
    with write(conn):
      _ = conn.execute(
        _CANCEL_QUEUED,
        (now, now, job_id),
      )
      _ = conn.execute(
        _CANCEL_RUNNING,
        (now, job_id),
      )
      job = row(conn, job_id)
    if job is None:
      raise NoSuchJobError(f"no job {job_id}")
    if job.state == "running" and (started := job.leader()) is not None:
      _ = signal_group(started, signal.SIGTERM)
  return job


def pause(config: Config, queue: str, reason: str | None = None) -> Pause:
  """Pause a queue: the scheduler starts none of its jobs until it resumes.

  Its queued jobs stay queued and its running ones run on. Once this
  returns, no tick starts a job of the queue. Pausing a paused queue
  changes nothing: its pause keeps when it began and why.

  Args:
    config: The queue file.
    queue: The queue, one the file names.
    reason: Why, for whoever lists the pauses.

  Returns:
    The queue's pause.

  Raises:
    ValueError: When the file names no such queue.
  """
  if queue not in config.queues:
    raise ValueError(f"no queue {queue} in the queue file")
  with connect(config.database) as conn, write(conn):
    _ = conn.execute(_PAUSE, (queue, time.time(), reason))
    return _paused(conn, "queue = ?", (queue,))[0]


def resume(config: Config, queue: str) -> Pause | None:
  """Resume a queue, so that its jobs start again as caps allow.

  A queue the file no longer names may be resumed while it is paused.

  Args:
    config: The queue file.
    queue: The queue.

  Returns:
    The pause it ended; None when the queue was not paused.

  Raises:
    ValueError: When the queue is neither paused nor named in the file.
  """
  with connect(config.database) as conn, write(conn):
    found = _paused(conn, "queue = ?", (queue,))
    if not found and queue not in config.queues:
      raise ValueError(f"no queue {queue} in the queue file")
    _ = conn.execute("DELETE FROM pauses WHERE queue = ?", (queue,))
  return found[0] if found else None


def paused_queues(conn: sqlite3.Connection) -> set[str]:
  """Give the queues that are paused.

  Args:
    conn: The connection.

  Returns:
    Their names.
  """
  return {pause.queue for pause in _paused(conn, "1", ())}


def pauses(config: Config) -> list[Pause]:
  """List the pauses, by queue.

  It opens the database read-only and writes nothing; a database made
  before queues could be paused has none.

  Args:
    config: The queue file.

  Returns:
    The pauses.

  Raises:
    FileNotFoundError: When the database does not exist yet.
  """  # noqa: DOC502
  tables = _read(
    config, "SELECT 1 FROM sqlite_master WHERE name = 'pauses'", ()
  )
  if not tables:
    return []
  sql = "SELECT * FROM pauses ORDER BY queue"
  return [_pause(found) for found in _read(config, sql, ())]


def _paused(
  conn: sqlite3.Connection, where: str, params: Sequence[object]
) -> list[Pause]:
  rows = cast(
    list[sqlite3.Row],
    conn.execute(
      f"SELECT * FROM pauses WHERE {where} ORDER BY queue", params
    ).fetchall(),
  )
  return [_pause(found) for found in rows]


def _pause(found: sqlite3.Row) -> Pause:
  return Pause(
    queue=cast(str, found["queue"]),
    paused_at=cast(float, found["paused_at"]),
    reason=cast(str | None, found["reason"]),
  )


def _check(
  config: Config, queue: str, argv: list[str], cwd: Path, memory_mib: int
) -> None:
  if queue not in config.queues:
    raise ValueError(f"no queue {queue} in the queue file")
  if not argv:
    raise ValueError("the command must not be empty")
  if not cwd.is_absolute() or not cwd.is_dir():
    raise ValueError(f"{cwd}: not an absolute path to a directory")
  if memory_mib < 0:
    raise ValueError("memory_mib must be at least 0")


def _job(found: sqlite3.Row) -> Job:
  return Job(
    id=cast(int, found["id"]),
    queue=cast(str, found["queue"]),
    argv=tuple(cast(list[str], json.loads(cast(str, found["argv"])))),
    cwd=cast(str, found["cwd"]),
    memory_mib=cast(int, found["memory_mib"]),
    dedup_key=cast(str | None, found["dedup_key"]),
    labels=cast(dict[str, str], json.loads(cast(str, found["labels"]))),
    state=cast(str, found["state"]),
    pid=cast(int | None, found["pid"]),
    starttime=cast(int | None, found["starttime"]),
    boot_id=cast(str | None, found["boot_id"]),
    exit_code=cast(int | None, found["exit_code"]),
    queued_at=cast(float, found["queued_at"]),
    started_at=cast(float | None, found["started_at"]),
    ended_at=cast(float | None, found["ended_at"]),
    cancel_requested_at=cast(float | None, found["cancel_requested_at"]),
    log=cast(str, found["log"]),
  )

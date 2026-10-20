"""The Monitor page's reads: a resource monitor's SQLite database.

The database is the one a project file's monitor.db names, in the format
whose queries are below: runs of a sampler, each with the machine's and a
batch's samples, the containers' samples and events. Each query reads one
run between t0 and t1, in seconds since the epoch.
"""

from collections.abc import Generator
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat
from typing import cast
from urllib.parse import quote

from dataforge.viz.paths import RefusedError

POINTS = 300
"""How many buckets a curve is cut into."""

BARS_LIMIT = 5000
"""The most container bars one window returns."""

RUNS = """
SELECT run_id, started_at, ended_at, stop_reason, source, pid, host,
  interval_s
FROM runs
ORDER BY started_at
"""

MACHINE = """
SELECT
  CAST((t - :t0) / :bucket_s AS INTEGER) AS bucket,
  min(t) AS t,
  avg(cpu_cores) AS cpu_cores_avg,
  max(cpu_cores) AS cpu_cores_max,
  avg(mem_total_bytes - mem_available_bytes) AS mem_used_bytes_avg,
  max(mem_total_bytes - mem_available_bytes) AS mem_used_bytes_max,
  max(swap_used_bytes) AS swap_used_bytes_max,
  max(load1) AS load1_max
FROM machine
WHERE run_id = :run_id AND t >= :t0 AND t < :t1
GROUP BY bucket
ORDER BY bucket
"""

BARS = """
WITH starts AS (
  SELECT container_id, min(t) AS start_t, name, image, run, item, attempt,
    state, visit, trial
  FROM events
  WHERE run_id = :run_id AND kind = 'container_start' AND t < :t1
  GROUP BY container_id
),
ends AS (
  SELECT container_id, max(t) AS end_t, exit_code
  FROM events
  WHERE run_id = :run_id AND kind = 'container_end'
  GROUP BY container_id
),
bars AS (
  SELECT s.*, e.end_t, e.exit_code,
    e.end_t IS NULL AS open,
    coalesce(e.end_t, (
      SELECT max(c.t) FROM container c
      WHERE c.container_id = s.container_id AND c.run_id = :run_id
    )) AS last_t
  FROM starts s LEFT JOIN ends e USING (container_id)
)
SELECT * FROM bars
WHERE last_t >= :t0
ORDER BY item, attempt, start_t
"""

_CURVE = """
WITH per_sample AS (
  SELECT min(t) AS t, sum(cpu_cores) AS cpu_cores, sum(mem_bytes) AS mem_bytes
  FROM container
  WHERE {key} AND run_id = :run_id AND t >= :t0 AND t < :t1
  GROUP BY sample
)
SELECT
  CAST((t - :t0) / :bucket_s AS INTEGER) AS bucket,
  min(t) AS t,
  avg(cpu_cores) AS cpu_cores_avg,
  max(cpu_cores) AS cpu_cores_max,
  avg(mem_bytes) AS mem_bytes_avg,
  max(mem_bytes) AS mem_bytes_max
FROM per_sample
GROUP BY bucket
ORDER BY bucket
"""

ATTEMPT = _CURVE.format(key="attempt = :attempt")
CONTAINER = _CURVE.format(key="container_id = :container_id")

BATCH = """
SELECT
  CAST((t - :t0) / :bucket_s AS INTEGER) AS bucket,
  min(t) AS t,
  max(attempts) AS attempts_max,
  avg(sessions) AS sessions_avg,
  max(sessions) AS sessions_max,
  max(pool_bytes) AS pool_bytes,
  avg(pool_reserved_bytes) AS pool_reserved_bytes_avg,
  max(pool_reserved_bytes) AS pool_reserved_bytes_max,
  max(pool_waiting) AS pool_waiting_max
FROM batch
WHERE run_id = :run_id AND t >= :t0 AND t < :t1
GROUP BY bucket
ORDER BY bucket
"""

PAUSES = """
SELECT s.t AS start_t, s.delay_s,
  coalesce((
    SELECT min(e.t) FROM events e
    WHERE e.run_id = s.run_id AND e.kind = 'pause_end' AND e.t >= s.t
  ), s.until) AS end_t
FROM events s
WHERE s.run_id = :run_id AND s.kind = 'pause_start' AND s.t < :t1
  AND end_t >= :t0
ORDER BY s.t
"""

# The end of a run that has not written ended_at: its last sample.
_LAST = "SELECT max(t) AS t FROM machine WHERE run_id = :run_id"

# What the page draws a run's window with: its bounds and the machine's size.
_RUN = """
SELECT started_at, ended_at, cpus, mem_total_bytes FROM runs
WHERE run_id = :run_id
"""


@contextmanager
def connect(db: Path) -> Generator[sqlite3.Connection]:
  """Open the database read-only, refusing what it must not open.

  The path is configuration and is opened as given, but the database and the
  -wal and -shm files beside it, which SQLite opens on its own, must not be
  symlinks or anything but regular files, so that no read or write of
  SQLite's reaches another file.

  Args:
    db: The database.

  Yields:
    A read-only connection, closed on exit.

  Raises:
    RefusedError: 503 with the reason when it cannot be opened or read.
  """
  for path, missing_ok in (
    (db, False),
    (Path(f"{db}-wal"), True),
    (Path(f"{db}-shm"), True),
  ):
    try:
      mode = os.lstat(path).st_mode
    except FileNotFoundError:
      if missing_ok:
        continue
      raise RefusedError(503, "the monitor database does not exist") from None
    except OSError as error:
      raise RefusedError(
        503, f"the monitor database: {error.strerror}"
      ) from None
    if not stat.S_ISREG(mode):
      kind = "a symlink" if stat.S_ISLNK(mode) else "not a regular file"
      raise RefusedError(503, f"the monitor database's {path.name} is {kind}")
  uri = f"file:{quote(str(db))}?mode=ro"
  try:
    connection = sqlite3.connect(uri, uri=True)
  except sqlite3.Error as error:
    raise RefusedError(503, _reason(error)) from None
  connection.row_factory = _row
  try:
    yield connection
  except sqlite3.Error as error:
    raise RefusedError(503, _reason(error)) from None
  finally:
    connection.close()


def runs(db: Path) -> list[dict[str, object]]:
  """List the runs, newest last.

  Args:
    db: The database.

  Returns:
    Each run's row of query (a).
  """
  with connect(db) as c:
    return list(c.execute(RUNS))


def window(
  db: Path, run_id: int, t0: float | None, t1: float | None
) -> dict[str, object]:
  """Read a run's curves, pauses and container bars in a window.

  Args:
    db: The database.
    run_id: The run.
    t0: The window's start; None for the run's start.
    t1: The window's end; None for the run's end, or its last sample while it
      has no end.

  Returns:
    The window as read: its bounds and bucket, the machine's CPUs and
    memory, the machine's and the batch's buckets, the pauses, at most
    BARS_LIMIT bars, those that start first, how many there are and when
    the first one left out starts.

  Raises:
    RefusedError: 404 when there is no such run; 400 when the window is
      empty.
  """
  with connect(db) as c:
    found = cast(
      dict[str, object] | None,
      c.execute(_RUN, {"run_id": run_id}).fetchone(),
    )
    if found is None:
      raise RefusedError(404, f"no monitor run {run_id}")
    started = cast(float, found["started_at"])
    ended = cast(float | None, found["ended_at"])
    if ended is None:
      last = cast(
        dict[str, object], c.execute(_LAST, {"run_id": run_id}).fetchone()
      )
      ended = cast(float | None, last["t"])
    start = started if t0 is None else t0
    end = t1 if t1 is not None else ended if ended is not None else started
    # A run with one sample, or none yet, still has a window to draw.
    if t0 is None and t1 is None and end <= start:
      end = start + 1
    if not end > start:
      raise RefusedError(400, "the window ends before it starts")
    args = _args(run_id, start, end)
    bars = list(c.execute(BARS, args))
    return {
      "run_id": run_id,
      "t0": start,
      "t1": end,
      "bucket_s": args["bucket_s"],
      "cpus": found["cpus"],
      "mem_total_bytes": found["mem_total_bytes"],
      "machine": list(c.execute(MACHINE, args)),
      "batch": list(c.execute(BATCH, args)),
      "pauses": list(c.execute(PAUSES, args)),
      **_cut(bars),
    }


def curve(
  db: Path,
  run_id: int,
  t0: float,
  t1: float,
  attempt: str | None,
  container: str | None,
) -> list[dict[str, object]]:
  """Read one attempt's or one container's curve, query (d).

  Args:
    db: The database.
    run_id: The run.
    t0: The window's start.
    t1: The window's end.
    attempt: The attempt, or None.
    container: The container's ID, or None; exactly one of the two is given.

  Returns:
    Its buckets.

  Raises:
    RefusedError: 400 when not exactly one of attempt and container is
      given, or the window is empty.
  """
  if (attempt is None) == (container is None):
    raise RefusedError(400, "give one of attempt and container")
  if not t1 > t0:
    raise RefusedError(400, "the window ends before it starts")
  args = _args(run_id, t0, t1)
  with connect(db) as c:
    if attempt is not None:
      found = c.execute(ATTEMPT, args | {"attempt": attempt})
    else:
      found = c.execute(CONTAINER, args | {"container_id": container})
    return list(found)


def _cut(bars: list[dict[str, object]]) -> dict[str, object]:
  # The bars that start first, so that what is left out is the window's last
  # stretch rather than whole items; they keep the query's order.
  by_start = sorted(
    range(len(bars)),
    key=lambda i: (
      cast(float, bars[i]["start_t"]),
      cast(str | None, bars[i]["item"]) or "",
      cast(str | None, bars[i]["attempt"]) or "",
    ),
  )
  kept = set(by_start[:BARS_LIMIT])
  left = by_start[BARS_LIMIT:]
  return {
    "bars": [b for i, b in enumerate(bars) if i in kept],
    "bars_total": len(bars),
    "bars_until": bars[left[0]]["start_t"] if left else None,
  }


def _row(cursor: sqlite3.Cursor, row: tuple[object, ...]) -> dict[str, object]:
  names = [cast(str, d[0]) for d in cursor.description]
  return dict(zip(names, row, strict=True))


def _args(run_id: int, t0: float, t1: float) -> dict[str, object]:
  return {"run_id": run_id, "t0": t0, "t1": t1, "bucket_s": (t1 - t0) / POINTS}


def _reason(error: sqlite3.Error) -> str:
  reason = f"the monitor database cannot be read: {error}"
  if "readonly" in str(error):
    # A reader of a WAL database writes its -shm file, so it needs to be able
    # to write the database's directory.
    reason += " (a reader needs write access to the database's directory)"
  return reason

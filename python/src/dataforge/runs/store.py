"""A dataset's store of attempts: one SQLite file in WAL mode, and its writes.

The table attempt has a row per attempt, running while a holder process runs
it, interrupted once its holder is gone with no outcome written, and ended
once its outcome is written, which never changes. A unique index lets one row
per item be running. Every write is an UPDATE conditioned on the state the
row is in and, for a running row, on its holder, in a BEGIN IMMEDIATE
transaction, so a writer that finds another got there first changes nothing
and is told so. Each change of a row's state adds a row to the table event in
the same transaction. Each call opens its own connection and closes it.
Attempts kept before the store are brought into it by add, ended or
interrupted as they were, and held by no process.

The schema and every statement that writes the store are in this file only.
The schema only grows: a column added has a default and no constraint, and
raises SCHEMA_MINOR, so that a writer of the version before runs as it did;
any other change raises SCHEMA_MAJOR. A store made before is not given a
column added since: the change that adds one also adds the code that adds it
to such a store. Either change also changes the version of the format
`dataforge.runs.store` (dataforge.formats), so that a switch of releases
drains the queue first. A store whose schema_major is newer than this
module's is read and never written (NewerSchemaError).
"""

from collections.abc import Generator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, UTC
import json
from pathlib import Path
import sqlite3
from typing import cast

from dataforge.curation.engine import Attempt
from dataforge.queue.proc import Leader

SCHEMA_MAJOR = 1
"""The schema's major version: what this module reads and writes."""

SCHEMA_MINOR = 0
"""The schema's minor version: the columns added since the major one."""

STATES = ("running", "interrupted", "ended")
"""The states an attempt's row may be in."""

STOPPED_BY = ("person", "lost")
"""How an interrupted attempt was stopped: asked for by a person, or not."""

_SCHEMA = f"""
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS meta (
  name TEXT PRIMARY KEY,
  value INTEGER NOT NULL
) STRICT;
INSERT OR IGNORE INTO meta (name, value)
  VALUES ('schema_major', {SCHEMA_MAJOR}), ('schema_minor', {SCHEMA_MINOR});
CREATE TABLE IF NOT EXISTS attempt (
  id TEXT PRIMARY KEY,
  item TEXT NOT NULL,
  root TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('running', 'interrupted', 'ended')),
  outcome TEXT,
  reason TEXT,
  machine_status TEXT,
  stopped_by TEXT CHECK (stopped_by IN ('person', 'lost')),
  record TEXT,
  cost_usd REAL,
  progress_at TEXT,
  holder_pid INTEGER,
  holder_starttime INTEGER,
  holder_boot_id TEXT,
  job_id INTEGER,
  job_db TEXT,
  resumable INTEGER,
  resume_problem TEXT,
  resume_checked_at TEXT,
  created_at TEXT NOT NULL,
  ended_at TEXT,
  settled_at TEXT,
  extra TEXT NOT NULL DEFAULT '{{}}'
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS one_running_per_item ON attempt (item)
  WHERE state = 'running';
CREATE INDEX IF NOT EXISTS attempt_item ON attempt (item, created_at);
CREATE TABLE IF NOT EXISTS event (
  attempt_id TEXT NOT NULL,
  at TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  by TEXT NOT NULL,
  detail TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS event_attempt ON event (attempt_id);
COMMIT;
"""

_HOLDER = "holder_pid = ? AND holder_starttime = ? AND holder_boot_id = ?"

_INSERT = """
INSERT INTO attempt (id, item, root, state, holder_pid, holder_starttime,
  holder_boot_id, job_id, job_db, created_at, progress_at, extra)
VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
"""

_ADD = """
INSERT INTO attempt (id, item, root, state, outcome, reason, machine_status,
  stopped_by, record, cost_usd, progress_at, job_id, job_db, resumable,
  resume_problem, resume_checked_at, created_at, ended_at, settled_at, extra)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_STEP = f"""
UPDATE attempt SET record = ?, machine_status = ?, cost_usd = ?,
  progress_at = ?
WHERE id = ? AND state = 'running' AND {_HOLDER}
"""

_END = f"""
UPDATE attempt SET state = 'ended', outcome = ?, reason = ?,
  machine_status = ?, record = ?, cost_usd = ?, ended_at = ?
WHERE id = ? AND state = 'running' AND {_HOLDER}
"""

_INTERRUPT = f"""
UPDATE attempt SET state = 'interrupted', stopped_by = ?, resumable = ?,
  resume_problem = ?, resume_checked_at = ?
WHERE id = ? AND state = 'running' AND {_HOLDER}
"""

_TAKE = """
UPDATE attempt SET state = 'running', stopped_by = NULL, resumable = NULL,
  resume_problem = NULL, resume_checked_at = NULL, holder_pid = ?,
  holder_starttime = ?, holder_boot_id = ?, job_id = ?, job_db = ?,
  progress_at = ?
WHERE id = ? AND state = 'interrupted'
"""

_ABANDON = """
UPDATE attempt SET state = 'ended', outcome = ?, reason = ?, ended_at = ?
WHERE id = ? AND state = 'interrupted' AND machine_status IS NULL
"""

_CHECK = """
UPDATE attempt SET resumable = ?, resume_problem = ?, resume_checked_at = ?,
  cost_usd = coalesce(?, cost_usd)
WHERE id = ? AND state = 'interrupted'
"""

_NEWER_RECORD = """
UPDATE attempt SET record = ?, machine_status = ?
WHERE id = ? AND state = 'interrupted'
"""

_SETTLE = """
UPDATE attempt SET settled_at = ?
WHERE id = ? AND state = 'ended' AND settled_at IS NULL
"""

_EVENT = """
INSERT INTO event (attempt_id, at, from_state, to_state, by, detail)
VALUES (?, ?, ?, ?, ?, ?)
"""

_CLEANUP = "from_state = 'ended' AND to_state = 'ended'"

_CLEANED = f"""
INSERT INTO event (attempt_id, at, from_state, to_state, by, detail)
SELECT ?, ?, 'ended', 'ended', ?, ?
WHERE NOT EXISTS (SELECT 1 FROM event WHERE attempt_id = ? AND {_CLEANUP})
"""

_UNCLEANED = f"""
SELECT * FROM attempt
WHERE state = 'ended' AND settled_at IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM event WHERE event.attempt_id = attempt.id AND {_CLEANUP}
  )
ORDER BY created_at, id
"""

_BUSY_MS = 30_000


_LISTING = (
  "id, item, state, outcome, reason, machine_status, stopped_by, cost_usd,"
  " progress_at, holder_pid, holder_starttime, holder_boot_id, resumable,"
  " resume_problem, created_at"
)
"""The columns of the table attempt that Store.listing reads."""


class NewerSchemaError(Exception):
  """A store whose schema_major is newer than this module's, so not written."""


@dataclass(frozen=True, slots=True)
class AttemptRow:
  """A row of the table attempt.

  Attributes:
    id: The attempt's ID, `<item>/<stamp>`.
    item: The data item it makes.
    root: Its directory.
    state: One of STATES.
    outcome: The dataset's word for how it ended, once it has.
    reason: Why it ended so, as the dataset says.
    machine_status: The record's status as last written.
    stopped_by: One of STOPPED_BY while it is interrupted, else None.
    record: The latest Attempt.to_json(); None before the first step.
    cost_usd: The record's budget.cost_usd as last written, or the dataset's
      meter as last read for an interrupted row: a snapshot.
    progress_at: When its holder last wrote a step, or took it.
    holder: The process that runs it, or ran it last.
    job_id: The queue job it runs in, if any.
    job_db: That job's queue database.
    resumable: Whether it may go on, as last checked; None when not checked.
    resume_problem: Why it may not go on.
    resume_checked_at: When resumable was last checked.
    created_at: When its row was inserted.
    ended_at: When its outcome was written.
    settled_at: When the dataset's on_ended returned for it.
    extra: The dataset's own fields.
  """

  id: str
  item: str
  root: str
  state: str
  outcome: str | None
  reason: str | None
  machine_status: str | None
  stopped_by: str | None
  record: dict[str, object] | None
  cost_usd: float | None
  progress_at: str | None
  holder: Leader | None
  job_id: int | None
  job_db: str | None
  resumable: bool | None
  resume_problem: str | None
  resume_checked_at: str | None
  created_at: str
  ended_at: str | None
  settled_at: str | None
  extra: dict[str, object] = field(default_factory=dict[str, object])

  def attempt(self) -> Attempt | None:
    """Read the row's record back.

    Returns:
      The attempt, or None when the row has no record.

    Raises:
      RecordError: When the record is not one Attempt.from_json reads.
    """  # noqa: DOC502
    if self.record is None:
      return None
    return Attempt.from_json(Path(self.root), self.record)

  def to_json(self) -> dict[str, object]:
    """Give the row as JSON, its holder as {pid, starttime, boot_id}.

    Returns:
      The mapping.
    """
    holder = self.holder
    return {
      "id": self.id,
      "item": self.item,
      "root": self.root,
      "state": self.state,
      "outcome": self.outcome,
      "reason": self.reason,
      "machine_status": self.machine_status,
      "stopped_by": self.stopped_by,
      "cost_usd": self.cost_usd,
      "progress_at": self.progress_at,
      "holder": None
      if holder is None
      else {
        "pid": holder.pid,
        "starttime": holder.start,
        "boot_id": holder.boot,
      },
      "job_id": self.job_id,
      "job_db": self.job_db,
      "resumable": self.resumable,
      "resume_problem": self.resume_problem,
      "resume_checked_at": self.resume_checked_at,
      "created_at": self.created_at,
      "ended_at": self.ended_at,
      "settled_at": self.settled_at,
      "extra": self.extra,
      "record": self.record,
    }


@dataclass(frozen=True, slots=True)
class ListingRow:
  """The columns of a row of the table attempt that a listing reads.

  It has no record and no extra, the two columns that are JSON a read has to
  parse; Store.listing leaves them unread.

  Attributes:
    id: The attempt's ID, `<item>/<stamp>`.
    item: The data item it makes.
    state: One of STATES.
    outcome: The dataset's word for how it ended, once it has.
    reason: Why it ended so, as the dataset says.
    machine_status: The record's status as last written.
    stopped_by: One of STOPPED_BY while it is interrupted, else None.
    cost_usd: The record's budget.cost_usd as last written, or the dataset's
      meter as last read for an interrupted row: a snapshot.
    progress_at: When its holder last wrote a step, or took it.
    holder: The process that runs it, or ran it last.
    resumable: Whether it may go on, as last checked; None when not checked.
    resume_problem: Why it may not go on.
    created_at: When its row was inserted.
  """

  id: str
  item: str
  state: str
  outcome: str | None
  reason: str | None
  machine_status: str | None
  stopped_by: str | None
  cost_usd: float | None
  progress_at: str | None
  holder: Leader | None
  resumable: bool | None
  resume_problem: str | None
  created_at: str


@dataclass(frozen=True, slots=True)
class Event:
  """A row of the table event: a change of an attempt's state, or a cleanup.

  Attributes:
    attempt_id: The attempt.
    at: When.
    from_state: The state before; None for the row's insert.
    to_state: The state after; the same as from_state for a cleanup.
    by: "runner", "reconcile", or "add" for a row added as it was.
    detail: What it was about, such as the outcome or what a cleanup left.
  """

  attempt_id: str
  at: str
  from_state: str | None
  to_state: str
  by: str
  detail: dict[str, object]


@dataclass(frozen=True, slots=True)
class QueueJob:
  """The queue job an attempt runs in.

  Attributes:
    id: The job's ID.
    db: Its queue's database.
  """

  id: int
  db: str


def now() -> str:
  """Give the time to write into the store.

  Returns:
    The UTC time as ISO 8601, with +00:00.
  """
  return datetime.now(UTC).isoformat()


class Store:
  """A dataset's store of attempts, at a path."""

  def __init__(self, path: Path) -> None:
    """Name the store; nothing is opened until a call needs it.

    Args:
      path: The SQLite file, made with its tables on the first write.
    """
    self.path: Path = path

  # Reads.

  def rows(
    self,
    *,
    item: str | None = None,
    states: Sequence[str] | None = None,
  ) -> list[AttemptRow]:
    """List rows, the oldest first.

    It opens the store read-only and writes nothing.

    Args:
      item: Only this item's rows; None for every item.
      states: Only rows in these of STATES; None for any.

    Returns:
      The rows, by created_at, then ID; none when the store does not exist
      yet.

    Raises:
      ValueError: When a state is not one of STATES.
    """
    where, params = ["1"], list[object]()
    if item is not None:
      where.append("item = ?")
      params.append(item)
    if states is not None:
      if unknown := sorted(set(states) - set(STATES)):
        raise ValueError(f"unknown state {unknown[0]}")
      where.append(f"state IN ({', '.join('?' * len(states))})")
      params.extend(states)
    sql = (
      f"SELECT * FROM attempt WHERE {' AND '.join(where)}"
      " ORDER BY created_at, id"
    )
    if not self.path.exists():
      return []
    with self._read() as conn:
      found = cast(list[sqlite3.Row], conn.execute(sql, params).fetchall())
    return [_row(one) for one in found]

  def listing(self, *, item: str | None = None) -> list[ListingRow]:
    """List the columns of rows that a listing reads, the oldest first.

    It opens the store read-only and writes nothing, and parses no JSON.

    Args:
      item: Only this item's rows; None for every item.

    Returns:
      The rows, by created_at, then ID; none when the store does not exist
      yet.
    """
    where, params = "1", list[object]()
    if item is not None:
      where, params = "item = ?", [item]
    sql = (
      f"SELECT {_LISTING} FROM attempt WHERE {where} ORDER BY created_at, id"
    )
    if not self.path.exists():
      return []
    with self._read() as conn:
      found = cast(list[sqlite3.Row], conn.execute(sql, params).fetchall())
    return [_listing_row(one) for one in found]

  def uncleaned(self) -> list[AttemptRow]:
    """List the settled rows that have no cleanup event.

    It opens the store read-only and writes nothing.

    Returns:
      The rows, by created_at, then ID; none when the store does not exist
      yet.
    """
    if not self.path.exists():
      return []
    with self._read() as conn:
      found = cast(list[sqlite3.Row], conn.execute(_UNCLEANED).fetchall())
    return [_row(one) for one in found]

  def get(self, attempt_id: str) -> AttemptRow | None:
    """Read one row.

    It opens the store read-only and writes nothing.

    Args:
      attempt_id: The attempt's ID.

    Returns:
      The row, or None when there is none with that ID or no store yet.
    """
    if not self.path.exists():
      return None
    with self._read() as conn:
      found = cast(
        sqlite3.Row | None,
        conn.execute(
          "SELECT * FROM attempt WHERE id = ?", (attempt_id,)
        ).fetchone(),
      )
    return None if found is None else _row(found)

  def events(self, attempt_id: str) -> list[Event]:
    """List an attempt's events, the oldest first.

    It opens the store read-only and writes nothing.

    Args:
      attempt_id: The attempt's ID.

    Returns:
      The events; none when the store does not exist yet.
    """
    if not self.path.exists():
      return []
    with self._read() as conn:
      found = cast(
        list[sqlite3.Row],
        conn.execute(
          "SELECT * FROM event WHERE attempt_id = ? ORDER BY rowid",
          (attempt_id,),
        ).fetchall(),
      )
    return [
      Event(
        attempt_id=cast(str, one["attempt_id"]),
        at=cast(str, one["at"]),
        from_state=cast(str | None, one["from_state"]),
        to_state=cast(str, one["to_state"]),
        by=cast(str, one["by"]),
        detail=cast(dict[str, object], json.loads(cast(str, one["detail"]))),
      )
      for one in found
    ]

  def schema(self) -> tuple[int, int]:
    """Read the store's schema version.

    Returns:
      Its schema_major and schema_minor.

    Raises:
      FileNotFoundError: When the store does not exist yet.
    """  # noqa: DOC502
    with self._read() as conn:
      return _version(conn)

  # Writes: each but create returns whether it changed the row.

  def create(self) -> None:
    """Make the store and its tables, when they are missing.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    with self._write():
      pass

  def insert(
    self,
    attempt_id: str,
    item: str,
    root: Path,
    holder: Leader,
    job: QueueJob | None,
    extra: Mapping[str, object] | None = None,
  ) -> bool:
    """Insert a running row, unless another row of the item is running.

    Args:
      attempt_id: The new attempt's ID.
      item: Its item.
      root: Its directory.
      holder: The process that runs it.
      job: The queue job it runs in, if any.
      extra: The dataset's own fields of the row; None for none.

    Returns:
      Whether it was inserted: False when the item has a running row, or the
      ID is taken.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    at = now()
    with self._write() as conn:
      try:
        _ = conn.execute(
          _INSERT,
          (
            attempt_id,
            item,
            str(root),
            *holder,
            None if job is None else job.id,
            None if job is None else job.db,
            at,
            at,
            json.dumps(dict(extra or {})),
          ),
        )
      except sqlite3.IntegrityError:
        return False
      _event(conn, attempt_id, None, "running", "runner", {})
    return True

  def add(self, row: AttemptRow) -> bool:
    """Add an ended or interrupted row as it is given, with no holder.

    It is how attempts kept before the store are brought into it. The row's
    event is from no state to its own, by "add".

    Args:
      row: The row; its holder is not written.

    Returns:
      Whether it was added: False when the ID is taken.

    Raises:
      ValueError: When the row is running; ended with no outcome or no
        ended_at; or interrupted with no stopped_by of STOPPED_BY, or with
        an outcome, an ended_at or a settled_at.
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC503
    if row.state not in ("ended", "interrupted"):
      raise ValueError(f"{row.id}: only an ended or interrupted row is added")
    if row.state == "ended" and row.outcome is None:
      raise ValueError(f"{row.id}: an ended row needs an outcome")
    if row.state == "ended" and row.ended_at is None:
      raise ValueError(f"{row.id}: an ended row needs an ended_at")
    if row.state == "interrupted" and row.stopped_by not in STOPPED_BY:
      raise ValueError(f"{row.id}: an interrupted row needs a stopped_by")
    if row.state == "interrupted" and row.outcome is not None:
      raise ValueError(f"{row.id}: an interrupted row has no outcome")
    if row.state == "interrupted" and row.ended_at is not None:
      raise ValueError(f"{row.id}: an interrupted row has no ended_at")
    if row.state == "interrupted" and row.settled_at is not None:
      raise ValueError(f"{row.id}: an interrupted row has no settled_at")
    with self._write() as conn:
      try:
        _ = conn.execute(
          _ADD,
          (
            row.id,
            row.item,
            row.root,
            row.state,
            row.outcome,
            row.reason,
            row.machine_status,
            row.stopped_by,
            None if row.record is None else json.dumps(row.record),
            row.cost_usd,
            row.progress_at,
            row.job_id,
            row.job_db,
            None if row.resumable is None else int(row.resumable),
            row.resume_problem,
            row.resume_checked_at,
            row.created_at,
            row.ended_at,
            row.settled_at,
            json.dumps(row.extra),
          ),
        )
      except sqlite3.IntegrityError:
        return False
      _event(conn, row.id, None, row.state, "add", {})
    return True

  def step(
    self,
    attempt_id: str,
    holder: Leader,
    record: Mapping[str, object],
    cost_usd: float,
  ) -> bool:
    """Write a running row's record, as its holder's on_step gets it.

    Args:
      attempt_id: The attempt.
      holder: Its holder.
      record: The record, Attempt.to_json(); its status is machine_status.
      cost_usd: Its budget.cost_usd.

    Returns:
      Whether it changed the row.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    with self._write() as conn:
      changed = conn.execute(
        _STEP,
        (
          json.dumps(record),
          record.get("status"),
          cost_usd,
          now(),
          attempt_id,
          *holder,
        ),
      ).rowcount
    return changed == 1

  def end(
    self,
    attempt_id: str,
    holder: Leader,
    *,
    by: str,
    outcome: str,
    reason: str | None,
    record: Mapping[str, object] | None,
    cost_usd: float | None,
  ) -> bool:
    """Write a running row's outcome: it is ended.

    Args:
      attempt_id: The attempt.
      holder: Its holder.
      by: "runner" or "reconcile".
      outcome: The dataset's word.
      reason: Why, as the dataset says.
      record: The final record, whose status is machine_status; None when
        none was written.
      cost_usd: Its budget.cost_usd.

    Returns:
      Whether it changed the row.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    status = None if record is None else record.get("status")
    with self._write() as conn:
      changed = conn.execute(
        _END,
        (
          outcome,
          reason,
          status,
          None if record is None else json.dumps(record),
          cost_usd,
          now(),
          attempt_id,
          *holder,
        ),
      ).rowcount
      if changed:
        detail = {"outcome": outcome, "reason": reason}
        _event(conn, attempt_id, "running", "ended", by, detail)
    return changed == 1

  def interrupt(
    self,
    attempt_id: str,
    holder: Leader,
    *,
    by: str,
    stopped_by: str,
    problem: str | None = None,
  ) -> bool:
    """Make a running row interrupted: its holder stopped, or is gone.

    Args:
      attempt_id: The attempt.
      holder: The holder it has.
      by: "runner" or "reconcile".
      stopped_by: One of STOPPED_BY.
      problem: Why it cannot go on, when that is known: resumable is then
        false; None leaves it not checked.

    Returns:
      Whether it changed the row.

    Raises:
      ValueError: When stopped_by is not one of STOPPED_BY.
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC503
    if stopped_by not in STOPPED_BY:
      raise ValueError(f"unknown stopped_by {stopped_by}")
    checked = None if problem is None else now()
    with self._write() as conn:
      changed = conn.execute(
        _INTERRUPT,
        (
          stopped_by,
          None if problem is None else 0,
          problem,
          checked,
          attempt_id,
          *holder,
        ),
      ).rowcount
      if changed:
        detail: dict[str, object] = {"stopped_by": stopped_by}
        if problem is not None:
          detail["problem"] = problem
        _event(conn, attempt_id, "running", "interrupted", by, detail)
    return changed == 1

  def take(
    self, attempt_id: str, holder: Leader, job: QueueJob | None, *, by: str
  ) -> bool:
    """Make an interrupted row running again, under a new holder.

    Its progress_at is set, and resumable is not checked any more.

    Args:
      attempt_id: The attempt.
      holder: The new holder.
      job: The queue job the new holder runs in, if any.
      by: "runner", going on with it, or "reconcile", ending it.

    Returns:
      Whether it changed the row: False when the row is not interrupted, or
      another row of its item is running.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    with self._write() as conn:
      try:
        changed = conn.execute(
          _TAKE,
          (
            *holder,
            None if job is None else job.id,
            None if job is None else job.db,
            now(),
            attempt_id,
          ),
        ).rowcount
      except sqlite3.IntegrityError:
        return False
      if changed:
        _event(conn, attempt_id, "interrupted", "running", by, {})
    return changed == 1

  def abandon(self, attempt_id: str, outcome: str, reason: str) -> bool:
    """End an interrupted row that no one goes on with.

    A row whose machine_status is set is not given up: its machine ended, and
    reconcile ends it with the machine's outcome.

    Args:
      attempt_id: The attempt.
      outcome: The dataset's word for an attempt given up.
      reason: Why, naming the attempt that takes its place.

    Returns:
      Whether it changed the row: False also when its machine_status is set.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    with self._write() as conn:
      changed = conn.execute(
        _ABANDON, (outcome, reason, now(), attempt_id)
      ).rowcount
      if changed:
        detail = {"outcome": outcome, "reason": reason}
        _event(conn, attempt_id, "interrupted", "ended", "runner", detail)
    return changed == 1

  def check(
    self,
    attempt_id: str,
    problem: str | None,
    cost_usd: float | None,
  ) -> bool:
    """Write whether an interrupted row may go on, and what it spent.

    Args:
      attempt_id: The attempt.
      problem: Why it may not go on; None when it may.
      cost_usd: What it spent, as the dataset's meter reads now; None keeps
        the value the row has.

    Returns:
      Whether it changed the row.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    with self._write() as conn:
      changed = conn.execute(
        _CHECK,
        (
          int(problem is None),
          problem,
          now(),
          cost_usd,
          attempt_id,
        ),
      ).rowcount
    return changed == 1

  def newer_record(self, attempt_id: str, record: Mapping[str, object]) -> bool:
    """Write into an interrupted row a record newer than the one it has.

    It is the record its holder wrote into the attempt's record file and
    could not write into the store.

    Args:
      attempt_id: The attempt.
      record: The record; its status is machine_status.

    Returns:
      Whether it changed the row.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    with self._write() as conn:
      changed = conn.execute(
        _NEWER_RECORD,
        (json.dumps(record), record.get("status"), attempt_id),
      ).rowcount
    return changed == 1

  def settle(self, attempt_id: str) -> bool:
    """Write that the dataset's on_ended returned for an ended row.

    Args:
      attempt_id: The attempt.

    Returns:
      Whether it changed the row: False when it is not ended or was settled
      already.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    with self._write() as conn:
      changed = conn.execute(_SETTLE, (now(), attempt_id)).rowcount
    return changed == 1

  def cleaned(
    self,
    attempt_id: str,
    left: Mapping[str, object],
    raised: str | None,
    *,
    by: str,
  ) -> bool:
    """Add the event of an ended attempt's cleanup, unless it has one.

    Args:
      attempt_id: The attempt.
      left: What the cleanup left, by kind.
      raised: What the cleanup raised, as `<type>: <message>`, if it did.
      by: "runner" or "reconcile".

    Returns:
      Whether it added the event: False when the attempt has one already.

    Raises:
      NewerSchemaError: When the store's schema_major is newer.
    """  # noqa: DOC502
    detail = json.dumps({"cleanup_left": dict(left), "raised": raised})
    with self._write() as conn:
      added = conn.execute(
        _CLEANED, (attempt_id, now(), by, detail, attempt_id)
      ).rowcount
    return added == 1

  def check_writable(self) -> None:
    """Check that this dataforge may write the store.

    Raises:
      NewerSchemaError: When the store's schema_major is newer than
        SCHEMA_MAJOR.
    """  # noqa: DOC502
    if self.path.exists():
      with self._read() as conn:
        self._writable(conn)

  def _writable(self, conn: sqlite3.Connection) -> None:
    major, _minor = _version(conn)
    if major > SCHEMA_MAJOR:
      newer = f"{major}, newer than this dataforge's {SCHEMA_MAJOR}"
      raise NewerSchemaError(f"{self.path}: schema_major {newer}; read only")

  @contextmanager
  def _write(self) -> Generator[sqlite3.Connection]:
    self.path.parent.mkdir(parents=True, exist_ok=True)
    with closing(
      sqlite3.connect(self.path, timeout=_BUSY_MS / 1000, autocommit=True)
    ) as conn:
      conn.row_factory = sqlite3.Row
      _ = conn.execute("PRAGMA journal_mode = WAL").fetchall()
      if not _has_meta(conn):
        _ = conn.executescript(_SCHEMA)
      _ = conn.execute("BEGIN IMMEDIATE")
      committed = False
      try:
        self._writable(conn)
        yield conn
        _ = conn.execute("COMMIT")
        committed = True
      finally:
        # An exception raised once COMMIT has returned, such as a signal's,
        # finds no transaction to roll back, and must go on as it is.
        if not committed and conn.in_transaction:
          _ = conn.execute("ROLLBACK")

  @contextmanager
  def _read(self) -> Generator[sqlite3.Connection]:
    if not self.path.exists():
      raise FileNotFoundError(f"{self.path}: no such store")
    uri = f"{self.path.resolve().as_uri()}?mode=ro"
    with closing(
      sqlite3.connect(uri, uri=True, timeout=_BUSY_MS / 1000)
    ) as conn:
      conn.row_factory = sqlite3.Row
      yield conn


def _has_meta(conn: sqlite3.Connection) -> bool:
  found = cast(
    list[tuple[int]],
    conn.execute(
      "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchall(),
  )
  return bool(found)


def _version(conn: sqlite3.Connection) -> tuple[int, int]:
  found = dict(
    cast(
      list[tuple[str, int]],
      conn.execute("SELECT name, value FROM meta").fetchall(),
    )
  )
  return found["schema_major"], found["schema_minor"]


def _event(
  conn: sqlite3.Connection,
  attempt_id: str,
  from_state: str | None,
  to_state: str,
  by: str,
  detail: Mapping[str, object],
) -> None:
  _ = conn.execute(
    _EVENT,
    (attempt_id, now(), from_state, to_state, by, json.dumps(detail)),
  )


def _holder(found: sqlite3.Row) -> Leader | None:
  pid = cast(int | None, found["holder_pid"])
  start = cast(int | None, found["holder_starttime"])
  boot = cast(str | None, found["holder_boot_id"])
  return (
    None
    if pid is None or start is None or boot is None
    else Leader(pid, start, boot)
  )


def _listing_row(found: sqlite3.Row) -> ListingRow:
  resumable = cast(int | None, found["resumable"])
  return ListingRow(
    id=cast(str, found["id"]),
    item=cast(str, found["item"]),
    state=cast(str, found["state"]),
    outcome=cast(str | None, found["outcome"]),
    reason=cast(str | None, found["reason"]),
    machine_status=cast(str | None, found["machine_status"]),
    stopped_by=cast(str | None, found["stopped_by"]),
    cost_usd=cast(float | None, found["cost_usd"]),
    progress_at=cast(str | None, found["progress_at"]),
    holder=_holder(found),
    resumable=None if resumable is None else bool(resumable),
    resume_problem=cast(str | None, found["resume_problem"]),
    created_at=cast(str, found["created_at"]),
  )


def _row(found: sqlite3.Row) -> AttemptRow:
  record = cast(str | None, found["record"])
  resumable = cast(int | None, found["resumable"])
  return AttemptRow(
    id=cast(str, found["id"]),
    item=cast(str, found["item"]),
    root=cast(str, found["root"]),
    state=cast(str, found["state"]),
    outcome=cast(str | None, found["outcome"]),
    reason=cast(str | None, found["reason"]),
    machine_status=cast(str | None, found["machine_status"]),
    stopped_by=cast(str | None, found["stopped_by"]),
    record=None
    if record is None
    else cast(dict[str, object], json.loads(record)),
    cost_usd=cast(float | None, found["cost_usd"]),
    progress_at=cast(str | None, found["progress_at"]),
    holder=_holder(found),
    job_id=cast(int | None, found["job_id"]),
    job_db=cast(str | None, found["job_db"]),
    resumable=None if resumable is None else bool(resumable),
    resume_problem=cast(str | None, found["resume_problem"]),
    resume_checked_at=cast(str | None, found["resume_checked_at"]),
    created_at=cast(str, found["created_at"]),
    ended_at=cast(str | None, found["ended_at"]),
    settled_at=cast(str | None, found["settled_at"]),
    extra=cast(dict[str, object], json.loads(cast(str, found["extra"]))),
  )

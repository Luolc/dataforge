"""reconcile: how the rows of a store of attempts converge.

It is one function, safe to run again and in several processes at once. With
no hooks it runs no dataset code: it makes each running row whose holder is
gone interrupted. With the dataset's hooks it also ends each interrupted row
whose machine has ended, after it claims the row, calls on_ended again for
each ended row that has no settled_at, and checks again whether the other
interrupted rows may go on and what they spent.
"""

from collections.abc import Collection
from contextlib import closing
import dataclasses
import logging
import os
from pathlib import Path
import sqlite3
from typing import cast

from dataforge.curation.engine import Attempt
from dataforge.queue.proc import alive, Leader, leader
from dataforge.runs.hooks import Hooks
from dataforge.runs.render import newer
from dataforge.runs.store import (
  AttemptRow,
  QueueJob,
  Store,
)

JOB_ID_ENV = "DATAFORGE_JOB_ID"
"""The variable that gives the ID of the queue job a command runs in."""

QUEUE_DB_ENV = "DATAFORGE_QUEUE_DB"
"""The variable that gives that job's queue database."""

_log = logging.getLogger(__name__)


def reconcile(
  store: Store,
  hooks: Hooks | None = None,
  recheck: Collection[str] | None = None,
) -> list[str]:
  """Make a store's rows converge, as far as the hooks given allow.

  A running row whose holder is not alive (dataforge.queue.proc.alive)
  becomes interrupted, stopped by a person when its queue job's cancellation
  was asked for, else lost. With hooks: before anything else is done with an
  interrupted row, a newer record in its record file (render.newer) is
  written into it; an interrupted row whose machine_status is set is claimed,
  running under this process, and only when the claim changed it, finalized
  and ended with the word hooks.outcome gives; on_ended is called for each
  ended row with no settled_at, which is then set; each other interrupted row
  of an item in recheck, or of any item, gets resumable and cost_usd again.

  Args:
    store: The store.
    hooks: The dataset's hooks; None to run no dataset code.
    recheck: The items whose interrupted rows are checked again; None for
      every item.

  Returns:
    A line for each change it made.

  Raises:
    NewerSchemaError: When the store's schema_major is newer than this
      dataforge's: nothing is changed.
  """  # noqa: DOC502
  store.check_writable()
  done = _interrupt_gone(store)
  if hooks is not None:
    done.extend(_with_hooks(store, hooks, recheck))
  return done


def _interrupt_gone(store: Store) -> list[str]:
  done: list[str] = []
  for row in store.rows(states=("running",)):
    holder = row.holder
    if holder is None or alive(holder):
      continue
    stopped_by = "person" if cancel_requested(job_of(row)) else "lost"
    if store.interrupt(row.id, holder, by="reconcile", stopped_by=stopped_by):
      done.append(f"{row.id}: running -> interrupted, {stopped_by}")
  return done


def _with_hooks(
  store: Store, hooks: Hooks, recheck: Collection[str] | None
) -> list[str]:
  done: list[str] = []
  me = this_process()
  for row in store.rows(states=("interrupted",)):
    fresher = newer(row, Path(row.root) / hooks.record)
    if fresher is not None and store.newer_record(row.id, fresher):
      status = fresher.get("status")
      row = dataclasses.replace(
        row,
        record=fresher,
        machine_status=status if isinstance(status, str) else None,
      )
      done.append(f"{row.id}: the record file's newer record")
    if row.machine_status is not None:
      done.extend(_end_claimed(store, hooks, row, me))
    elif recheck is None or row.item in recheck:
      done.extend(_check(store, hooks, row))
  for row in store.rows(states=("ended",)):
    if row.settled_at is None and _settled(hooks, store, row):
      done.append(f"{row.id}: settled")
  return done


def settle(store: Store, hooks: Hooks, row: AttemptRow) -> bool:
  """Call the dataset's on_ended for an ended row, then set its settled_at.

  Args:
    store: The store.
    hooks: The dataset's hooks.
    row: The row, ended.

  Returns:
    Whether settled_at was set by this call.
  """
  hooks.on_ended(row)
  return store.settle(row.id)


def outcome_of(
  hooks: Hooks, record: Attempt | None, error: Exception | None
) -> tuple[str, str | None]:
  """Give an ended attempt's word and reason for its row.

  Args:
    hooks: The dataset's hooks.
    record: The attempt's record; None when it has none.
    error: What a state or set_up raised, if anything did.

  Returns:
    The word hooks.outcome gives, and the record's own reason, the
    machine's, when it has one, else the reason hooks.outcome gives.
  """
  outcome, reason = hooks.outcome(record, error)
  if record is not None and record.reason is not None:
    reason = record.reason
  return outcome, reason


def this_process() -> Leader:
  """Give this process, as a holder of a row.

  Returns:
    Its pid, starttime and boot ID.

  Raises:
    RuntimeError: When /proc does not show it.
  """
  me = leader(os.getpid())
  if me is None:
    raise RuntimeError("/proc does not show this process")
  return me


def job_from_env() -> QueueJob | None:
  """Give the queue job this process runs in, as the queue's wrapper says.

  Returns:
    The job, from DATAFORGE_JOB_ID and DATAFORGE_QUEUE_DB; None when either
    is unset or empty.

  Raises:
    ValueError: When DATAFORGE_JOB_ID is not an integer.
  """  # noqa: DOC502
  job_id, db = os.environ.get(JOB_ID_ENV), os.environ.get(QUEUE_DB_ENV)
  if not job_id or not db:
    return None
  return QueueJob(int(job_id), db)


def job_of(row: AttemptRow) -> QueueJob | None:
  """Give the queue job a row ran in.

  Args:
    row: The row.

  Returns:
    The job; None when it ran in none.
  """
  if row.job_id is None or row.job_db is None:
    return None
  return QueueJob(row.job_id, row.job_db)


def cancel_requested(job: QueueJob | None) -> bool:
  """Tell whether a person asked the queue to cancel a job.

  It opens the queue's database read-only.

  Args:
    job: The job; None for none.

  Returns:
    True when the job's cancel_requested_at is set; False when it is not,
    there is no job, or its database cannot be read, which is logged.
  """
  if job is None:
    return False
  uri = f"{Path(job.db).resolve().as_uri()}?mode=ro"
  try:
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn:
      found = cast(
        tuple[float | None] | None,
        conn.execute(
          "SELECT cancel_requested_at FROM jobs WHERE id = ?", (job.id,)
        ).fetchone(),
      )
  except sqlite3.Error as error:
    _log.warning("job %d in %s: %s", job.id, job.db, error)
    return False
  return found is not None and found[0] is not None


def _end_claimed(
  store: Store, hooks: Hooks, row: AttemptRow, me: Leader
) -> list[str]:
  # The claim makes the row this process's, so that of two that reconcile it
  # at once only one finalizes it.
  if not store.take(row.id, me, job_of(row), by="reconcile"):
    return []
  held = dataclasses.replace(row, state="running", holder=me, stopped_by=None)
  try:
    record = held.attempt()
    outcome, reason = outcome_of(hooks, record, None)
    hooks.finalize(held, record)
  # The dataset's code may raise anything; the row is then given back for a
  # later reconcile.
  except Exception:  # noqa: BLE001
    _log.exception("%s: not ended; it is interrupted again", row.id)
    stopped_by = row.stopped_by or "lost"
    _ = store.interrupt(row.id, me, by="reconcile", stopped_by=stopped_by)
    return [f"{row.id}: claimed, not ended"]
  if not store.end(
    row.id,
    me,
    by="reconcile",
    outcome=outcome,
    reason=reason,
    record=row.record,
    cost_usd=row.cost_usd,
  ):
    return []
  done = [f"{row.id}: interrupted -> ended, {outcome}"]
  ended = store.get(row.id)
  if ended is not None and _settled(hooks, store, ended):
    done.append(f"{row.id}: settled")
  return done


def _check(store: Store, hooks: Hooks, row: AttemptRow) -> list[str]:
  try:
    problem = hooks.resumable(row)
    cost = hooks.meter(row)
  # The dataset's code may raise anything; the other rows are checked all
  # the same.
  except Exception:  # noqa: BLE001
    _log.exception("%s: not checked", row.id)
    return []
  if not store.check(row.id, problem, cost):
    return []
  return [f"{row.id}: resumable {'no, ' + problem if problem else 'yes'}"]


def _settled(hooks: Hooks, store: Store, row: AttemptRow) -> bool:
  try:
    return settle(store, hooks, row)
  # The dataset's code may raise anything; a later reconcile calls it again.
  except Exception:  # noqa: BLE001
    _log.exception("%s: on_ended failed; it is not settled", row.id)
    return False

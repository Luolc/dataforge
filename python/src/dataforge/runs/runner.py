"""run_attempt: run one attempt of a data item, its row in the store kept.

The runner holds the item's one running row while it runs the dataset's
machine. It writes the row at each step, then the record file. When the
machine returns or a state raises, the dataset's outcome gives the word and
finalize keeps what the attempt keeps; one transaction then writes the row
ended; on_ended follows, and only then the cleanup, which adds an event and
changes no outcome. When SIGTERM or SIGINT stops it, the row is made
interrupted first, then on_stop runs and resumable is written.
"""

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager, ExitStack
import dataclasses
from datetime import datetime, UTC
import logging
from pathlib import Path
import signal
import sqlite3
import threading
import time
from types import FrameType
from typing import Literal

from dataforge.curation.engine import Attempt
from dataforge.queue.proc import Leader
from dataforge.runs.hooks import Hooks
from dataforge.runs.reconcile import (
  cancel_requested,
  clean_up,
  job_from_env,
  outcome_of,
  reconcile,
  settle,
  this_process,
)
from dataforge.runs.render import write
from dataforge.runs.store import (
  AttemptRow,
  Event,
  NewerSchemaError,
  now,
  QueueJob,
  Store,
)

type GoOn = Literal["when_it_can", "only", "never"]

END_RETRIES_S = (1.0, 2.0, 4.0)
"""The waits before each retry of the write of an attempt's end."""

EXIT_UNWRITTEN = 75
"""The exit status of a process that could not write an attempt's end."""

_STORE_ERRORS = (sqlite3.Error, NewerSchemaError)

_log = logging.getLogger(__name__)


class StartError(Exception):
  """An attempt that did not start, and why; nothing of it ran."""


class _UnwrittenError(Exception):
  """The end of an attempt that could not be written."""


def run_attempt(
  store: Store,
  item: str,
  hooks: Hooks,
  *,
  go_on: GoOn = "when_it_can",
  extra: Mapping[str, object] | None = None,
) -> AttemptRow:
  """Run one attempt of an item, going on with an interrupted one or not.

  It first reconciles the store with the hooks, rechecking only the item's
  rows. go_on picks what it runs: "when_it_can" goes on with the item's
  newest interrupted row when it is resumable and its take_over succeeds,
  else starts a new attempt; "only" goes on with it or raises StartError;
  "never" starts a new one. The row it goes on with is made running under
  this process before take_over runs, so of two that go on with one row only
  one takes it over; a take_over that fails makes the row interrupted again,
  not resumable. Every other interrupted row of the item, and the one it
  could not go on with when it starts a new one, is ended with
  hooks.given_up, or hooks.canceled when a person stopped it and the dataset
  gives that word, settled and cleaned up. After the cleanup it reconciles the
  whole store. A new attempt's row is inserted with extra; one it goes on
  with keeps the extra it has.

  The end is written in one transaction, retried after each wait of
  END_RETRIES_S; when it still fails, the process exits EXIT_UNWRITTEN with
  the row running, for reconcile to end it from the record. While it runs in
  the main thread, SIGTERM raises SystemExit, once.

  Args:
    store: The dataset's store.
    item: The data item.
    hooks: The dataset's hooks.
    go_on: Whether it goes on with an interrupted attempt.
    extra: The dataset's own fields of a new attempt's row; None for none.

  Returns:
    The attempt's row, ended.

  Raises:
    StartError: When another attempt of the item runs, or go_on is "only"
      and there is none to go on with.
    SystemExit: For SIGTERM, after the row is interrupted; with
      EXIT_UNWRITTEN when the end could not be written.
    KeyboardInterrupt: For SIGINT, after the row is interrupted.
  """  # noqa: DOC502
  _ = reconcile(store, hooks, recheck=[item])
  with _sigterm_raises():
    ended = _Runner(store, hooks, extra).run(item, go_on)
  # The attempt has ended and is written; a store that cannot be reconciled
  # now is reconciled by the next attempt or the command.
  try:
    _ = reconcile(store, hooks)
  except Exception:  # noqa: BLE001
    _log.exception("the reconcile after %s", ended.id)
  return ended


class _Runner:
  def __init__(
    self, store: Store, hooks: Hooks, extra: Mapping[str, object] | None
  ) -> None:
    self.store: Store = store
    self.hooks: Hooks = hooks
    self.extra: Mapping[str, object] | None = extra
    self.me: Leader = this_process()
    self.job: QueueJob | None = job_from_env()
    # The row this process holds, as it last wrote it, once it holds one.
    self.row: AttemptRow | None = None
    self.last: Attempt | None = None

  def run(self, item: str, go_on: GoOn) -> AttemptRow:
    stack = ExitStack()
    try:
      self._start(item, go_on)
      record, error = self._machine(stack)
      ended = self._end(record, error)
    except BaseException as stop:
      if not isinstance(stop, _UnwrittenError):
        self._stop(stop)
      # A context manager of the dataset's sees how the attempt stopped.
      _ = stack.__exit__(type(stop), stop, stop.__traceback__)
      if isinstance(stop, _UnwrittenError):
        raise SystemExit(EXIT_UNWRITTEN) from stop
      raise
    self._clean_up(stack, ended)
    return ended

  def _start(self, item: str, go_on: GoOn) -> None:
    rows = self.store.rows(item=item)
    if running := [row for row in rows if row.state == "running"]:
      raise StartError(f"{item}: {running[0].id} runs")
    # A row whose machine ended is reconcile's to end with its outcome.
    waiting = [
      row
      for row in rows
      if row.state == "interrupted" and row.machine_status is None
    ]
    target = waiting[-1] if waiting and go_on != "never" else None
    if go_on == "only":
      if target is None:
        raise StartError(f"{item}: no interrupted attempt to go on with")
      if not target.resumable:
        why = target.resume_problem or "not checked"
        raise StartError(f"{target.id} may not go on: {why}")
    if target is not None and target.resumable:
      self._go_on(target, go_on)
    if self.row is None:
      self._insert(item)
    assert self.row is not None
    for row in waiting:
      if row.id != self.row.id:
        self._abandon(row)

  def _go_on(self, target: AttemptRow, go_on: GoOn) -> None:
    if not self.store.take(target.id, self.me, self.job, by="runner"):
      raise StartError(f"{target.id}: another process took it")
    self.row = dataclasses.replace(
      target,
      state="running",
      stopped_by=None,
      holder=self.me,
      job_id=None if self.job is None else self.job.id,
      job_db=None if self.job is None else self.job.db,
    )
    problem = self.hooks.take_over(self.row)
    if problem is None:
      return
    stopped_by = target.stopped_by or "lost"
    _ = self.store.interrupt(
      target.id, self.me, by="runner", stopped_by=stopped_by, problem=problem
    )
    self.row = None
    if go_on == "only":
      raise StartError(f"{target.id} may not go on: {problem}")

  def _insert(self, item: str) -> None:
    attempt_id = _new_id(self.store, item)
    root = self.hooks.root(attempt_id)
    if not self.store.insert(
      attempt_id, item, root, self.me, self.job, self.extra
    ):
      raise StartError(f"{item}: another attempt of it runs")
    self.row = self.store.get(attempt_id)

  def _abandon(self, row: AttemptRow) -> None:
    assert self.row is not None
    # Read again: a take_over that failed has given it a problem since.
    row = self.store.get(row.id) or row
    why = "Started again as a new attempt"
    if row.resume_problem:
      why += f": {row.resume_problem}"
    why += f" ({self.row.id})"
    word = self.hooks.given_up
    if row.stopped_by == "person" and self.hooks.canceled is not None:
      word = self.hooks.canceled
    if self.store.abandon(row.id, word, why):
      ended = self.store.get(row.id)
      if ended is None:
        return
      try:
        clean = settle(self.store, self.hooks, ended)
      # The new attempt goes on; reconcile calls the dataset's on_ended again.
      except Exception:  # noqa: BLE001
        _log.exception(
          "%s: on_ended failed; reconcile calls it again", ended.id
        )
        clean = True
      if clean:
        _ = clean_up(self.store, self.hooks, ended, by="runner")

  def _machine(
    self, stack: ExitStack
  ) -> tuple[Attempt | None, Exception | None]:
    assert self.row is not None
    try:
      go = self.hooks.set_up(self.row, self._on_step, stack)
      return go(), None
    # Whatever the dataset's set up or a state raises ends the attempt with
    # the dataset's word for it, so that what it spent is recorded.
    except Exception as error:  # noqa: BLE001
      _log.exception("%s raised", self.row.id)
      return self.last, error

  def _on_step(self, attempt: Attempt) -> None:
    assert self.row is not None
    self.last = attempt
    record = attempt.to_json()
    self.row = dataclasses.replace(
      self.row,
      record=record,
      machine_status=attempt.status,
      cost_usd=attempt.cost_usd,
      progress_at=now(),
    )
    # A step not written is written whole by the next one.
    try:
      if not self.store.step(self.row.id, self.me, record, attempt.cost_usd):
        _log.warning("%s: the row is not running here", self.row.id)
    except _STORE_ERRORS as error:
      _log.warning("%s: the step was not written: %s", self.row.id, error)
    self._write_record(self.row, [])

  def _end(self, record: Attempt | None, error: Exception | None) -> AttemptRow:
    assert self.row is not None
    outcome, reason = outcome_of(self.hooks, record, error)
    self.hooks.finalize(self.row, record)
    # With no step, as when set_up raised, the row keeps the record it has.
    ended = dataclasses.replace(
      self.row, state="ended", outcome=outcome, reason=reason, ended_at=now()
    )
    if record is not None:
      ended = dataclasses.replace(
        ended,
        record=record.to_json(),
        machine_status=record.status,
        cost_usd=record.cost_usd,
      )
    # The record file has the end even when the store cannot be written.
    self._write_record(ended, [])
    self._write_end(ended, outcome)
    self.row = self.store.get(ended.id) or ended
    try:
      _ = settle(self.store, self.hooks, self.row)
    # The outcome is written; reconcile calls the dataset's on_ended again.
    except Exception:  # noqa: BLE001
      _log.exception("%s: on_ended failed; reconcile calls it again", ended.id)
    self.row = self.store.get(ended.id) or self.row
    return self.row

  def _write_end(self, ended: AttemptRow, outcome: str) -> None:
    for wait in (*END_RETRIES_S, None):
      try:
        if self.store.end(
          ended.id,
          self.me,
          by="runner",
          outcome=outcome,
          reason=ended.reason,
          record=ended.record,
          cost_usd=ended.cost_usd,
        ):
          return
        # Not running under this process: asking again changes nothing.
        raise _UnwrittenError(f"{ended.id}: the row is not running here")
      except _STORE_ERRORS as error:
        _log.warning("%s: the end was not written: %s", ended.id, error)
      if wait is None:
        raise _UnwrittenError(ended.id)
      time.sleep(wait)

  def _stop(self, stop: BaseException) -> None:
    # Stopped before the end was written: the row is made interrupted before
    # anything else, so that a reader never takes it for one that runs.
    row = self.row
    if row is None:
      return
    person = isinstance(stop, KeyboardInterrupt) or cancel_requested(self.job)
    stopped_by = "person" if person else "lost"
    try:
      if not self.store.interrupt(
        row.id, self.me, by="runner", stopped_by=stopped_by
      ):
        return
    except _STORE_ERRORS as error:
      _log.warning("%s: not written interrupted: %s", row.id, error)
    row = dataclasses.replace(row, state="interrupted", stopped_by=stopped_by)
    try:
      self.hooks.on_stop(row)
      _ = self.store.check(
        row.id, self.hooks.resumable(row), self.hooks.meter(row)
      )
    # The signal's exception goes on whatever the dataset's code raises.
    except Exception:  # noqa: BLE001
      _log.exception("%s: stopping it", row.id)

  def _clean_up(self, stack: ExitStack, ended: AttemptRow) -> None:
    raised: list[str] = []
    left: Mapping[str, object] = {}
    # A cleanup changes no outcome, whatever it raises: it adds an event.
    try:
      stack.close()
    except Exception as error:  # noqa: BLE001
      raised.append(f"{type(error).__name__}: {error}")
    try:
      left = self.hooks.cleanup(ended)
    except Exception as error:  # noqa: BLE001
      raised.append(f"{type(error).__name__}: {error}")
    for each in raised:
      _log.warning("%s: the cleanup raised %s", ended.id, each)
    try:
      _ = self.store.cleaned(
        ended.id, left, "; ".join(raised) or None, by="runner"
      )
      events = self.store.events(ended.id)
    except _STORE_ERRORS as error:
      _log.warning("%s: the cleanup was not written: %s", ended.id, error)
      return
    self._write_record(ended, events)

  def _write_record(self, row: AttemptRow, events: list[Event]) -> None:
    path = Path(row.root) / self.hooks.record
    try:
      write(path, self.hooks.render(row, events))
    except OSError as error:
      _log.warning("%s: the record file was not written: %s", row.id, error)


def _new_id(store: Store, item: str) -> str:
  # An attempt of the item already made this second has the ID taken.
  while True:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    attempt_id = f"{item}/{stamp}"
    if store.get(attempt_id) is None:
      return attempt_id
    time.sleep(0.1)


@contextmanager
def _sigterm_raises() -> Generator[None]:
  if threading.current_thread() is not threading.main_thread():
    yield
    return
  raised = False

  def _term(_sig: int, _frame: FrameType | None) -> None:
    # Once only: a second SIGTERM must not cut short what the first started.
    nonlocal raised
    if not raised:
      raised = True
      raise SystemExit(128 + signal.SIGTERM)

  before: Callable[[int, FrameType | None], object] | int | None = (
    signal.signal(signal.SIGTERM, _term)
  )
  try:
    yield
  finally:
    _ = signal.signal(signal.SIGTERM, before)

from collections.abc import Callable, Sequence
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time
from typing import cast, override

import pytest

from dataforge.curation import Outcome
from dataforge.queue.proc import Leader
from dataforge.runs import (
  AttemptRow,
  EXIT_UNWRITTEN,
  JOB_ID_ENV,
  QUEUE_DB_ENV,
  reconcile,
  run_attempt,
  runner,
  StartError,
  Store,
)
from tests.runs.conftest import (
  after,
  at_once,
  failing,
  queue_db,
  visits,
)
from tests.runs.fake import FakeHooks, KEPT, WHOLE, Work

ITEM = "item-1"


def _only(store: Store) -> AttemptRow:
  [row] = store.rows()
  return row


def _stopped(store: Store, hooks: FakeHooks) -> AttemptRow:
  # An attempt that SIGTERM stopped while its state ran: interrupted.
  hooks.sigterm = True
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks)
  hooks.sigterm = False
  return _only(store)


def test_an_attempt_ends_with_the_outcome_and_its_record_file(
  store: Store, hooks: FakeHooks
) -> None:
  ended = run_attempt(store, ITEM, hooks)
  assert (ended.state, ended.outcome, ended.machine_status) == (
    "ended",
    "passed",
    "passed",
  )
  assert ended.settled_at is not None
  assert ended.id.startswith(f"{ITEM}/")
  assert ended.root == str(hooks.root(ended.id))
  assert len(visits(ended.record)) == 1
  root = Path(ended.root)
  assert (root / KEPT).read_text() == WHOLE
  written = cast(
    dict[str, object], json.loads((root / hooks.record).read_text())
  )
  assert (written["status"], written["machine_status"]) == ("passed", "passed")
  assert visits(written) == visits(ended.record)
  events = store.events(ended.id)
  assert [(e.from_state, e.to_state) for e in events] == [
    (None, "running"),
    ("running", "ended"),
    ("ended", "ended"),
  ]


def test_finalize_comes_before_the_end_and_on_ended_and_cleanup_after(
  store: Store, hooks: FakeHooks
) -> None:
  ended = run_attempt(store, ITEM, hooks)
  order = [line.split()[0] for line in hooks.calls()]
  assert order == ["set_up", "finalize", "on_ended", "stack", "cleanup"]
  [end, cleaned] = store.events(ended.id)[1:]
  assert (end.to_state, end.detail) == (
    "ended",
    {"outcome": "passed", "reason": None},
  )
  assert cleaned.detail == {"cleanup_left": {}, "raised": None}


@pytest.mark.parametrize(
  ("left", "raises"),
  [
    ({"containers": 2}, None),
    ({}, subprocess.TimeoutExpired(["docker", "rm"], 120)),
  ],
)
def test_a_cleanup_that_raises_or_leaves_something_changes_no_outcome(
  store: Store,
  hooks: FakeHooks,
  left: dict[str, object],
  raises: BaseException | None,
) -> None:
  hooks.left, hooks.cleanup_raises = left, raises
  ended = run_attempt(store, ITEM, hooks)
  row = _only(store)
  assert (row.state, row.outcome, row.reason) == ("ended", "passed", None)
  assert row == ended
  cleaned = store.events(row.id)[-1]
  # The cleanup's event comes after the end's: the outcome was written first.
  assert [(e.from_state, e.to_state) for e in store.events(row.id)] == [
    (None, "running"),
    ("running", "ended"),
    ("ended", "ended"),
  ]
  assert cleaned.detail["cleanup_left"] == left
  raised = cleaned.detail["raised"]
  if raises is None:
    assert raised is None
  else:
    assert isinstance(raised, str)
    assert raised.startswith("TimeoutExpired: ")


def test_an_exception_of_a_state_ends_the_attempt_with_the_datasets_word(
  store: Store, hooks: FakeHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
  def broken(*_args: object) -> object:
    raise OSError("no image")

  monkeypatch.setattr(FakeHooks, "set_up", broken)
  ended = run_attempt(store, ITEM, hooks)
  assert (ended.state, ended.outcome, ended.reason) == (
    "ended",
    "error",
    "OSError: no image",
  )


@pytest.mark.parametrize(
  ("canceled", "stopped_by"),
  [(True, "person"), (False, "lost"), (None, "lost")],
)
def test_on_sigterm_the_row_is_interrupted_before_on_stop_runs(
  store: Store,
  hooks: FakeHooks,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  canceled: bool | None,
  stopped_by: str,
) -> None:
  if canceled is not None:
    monkeypatch.setenv(JOB_ID_ENV, "1")
    monkeypatch.setenv(
      QUEUE_DB_ENV, str(queue_db(tmp_path / "q.sqlite", {1: canceled}))
    )
  before = signal.getsignal(signal.SIGTERM)
  row = _stopped(store, hooks)
  assert (row.state, row.stopped_by) == ("interrupted", stopped_by)
  # on_stop read the row from the store: it was interrupted already.
  [on_stop] = hooks.hook_calls("on_stop")
  assert on_stop.split()[-1] == "interrupted"
  # The stack set_up was given saw the stop, to keep what goes on.
  assert hooks.hook_calls("stack") == [f"stack {row.id} SystemExit"]
  assert (row.resumable, row.cost_usd) == (True, 1.5)
  assert not hooks.hook_calls("finalize")
  assert signal.getsignal(signal.SIGTERM) is before


def test_sigint_interrupts_the_row_as_stopped_by_a_person(
  store: Store, hooks: FakeHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
  def interrupted(*_args: object) -> object:
    raise KeyboardInterrupt

  monkeypatch.setattr(FakeHooks, "set_up", interrupted)
  with pytest.raises(KeyboardInterrupt):
    _ = run_attempt(store, ITEM, hooks)
  row = _only(store)
  assert (row.state, row.stopped_by) == ("interrupted", "person")


def test_go_on_takes_the_interrupted_row_over_and_ends_it(
  store: Store, hooks: FakeHooks
) -> None:
  stopped = _stopped(store, hooks)
  ended = run_attempt(store, ITEM, hooks, go_on="only")
  assert (ended.id, ended.state, ended.outcome) == (
    stopped.id,
    "ended",
    "passed",
  )
  assert hooks.hook_calls("take_over") == [f"take_over {stopped.id}"]
  record = ended.record
  assert record is not None
  assert len(cast(list[object], record["resumptions"])) == 1


def test_a_new_row_gets_the_extra_given_and_one_gone_on_with_keeps_its_own(
  store: Store, hooks: FakeHooks
) -> None:
  hooks.sigterm = True
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks, extra={"tone": "plain"})
  hooks.sigterm = False
  assert _only(store).extra == {"tone": "plain"}
  hooks.extras.clear()
  gone_on = run_attempt(store, ITEM, hooks, go_on="only", extra={"tone": "x"})
  assert gone_on.extra == {"tone": "plain"}
  assert hooks.extras == [
    ("take_over", {"tone": "plain"}),
    ("set_up", {"tone": "plain"}),
  ]
  new = run_attempt(store, ITEM, hooks, go_on="never", extra={"tone": "x"})
  assert new.extra == {"tone": "x"}


def test_only_raises_when_there_is_nothing_to_go_on_with(
  store: Store, hooks: FakeHooks
) -> None:
  with pytest.raises(StartError, match="no interrupted attempt"):
    _ = run_attempt(store, ITEM, hooks, go_on="only")
  hooks.problem = "its container is gone"
  _ = _stopped(store, hooks)
  with pytest.raises(StartError, match="its container is gone"):
    _ = run_attempt(store, ITEM, hooks, go_on="only")
  assert _only(store).state == "interrupted"


def test_a_take_over_that_fails_leaves_the_row_interrupted_not_resumable(
  store: Store, hooks: FakeHooks
) -> None:
  stopped = _stopped(store, hooks)
  hooks.take_over_problem = "the container would not start"
  with pytest.raises(StartError, match="would not start"):
    _ = run_attempt(store, ITEM, hooks, go_on="only")
  row = _only(store)
  assert (row.id, row.state, row.resumable, row.resume_problem) == (
    stopped.id,
    "interrupted",
    False,
    "the container would not start",
  )
  assert row.stopped_by == stopped.stopped_by


def test_when_it_can_starts_anew_and_gives_up_the_row_it_could_not_take(
  store: Store, hooks: FakeHooks
) -> None:
  stopped = _stopped(store, hooks)
  hooks.take_over_problem = "the container would not start"
  ended = run_attempt(store, ITEM, hooks)
  old = store.get(stopped.id)
  assert old is not None
  assert (old.state, old.outcome) == ("ended", hooks.given_up)
  assert old.reason == (
    "Started again as a new attempt: the container would not start "
    f"({ended.id})"
  )
  assert old.settled_at is not None
  assert ended.id != stopped.id
  assert ended.outcome == "passed"


def test_never_gives_up_each_interrupted_row_and_starts_anew(
  store: Store, hooks: FakeHooks
) -> None:
  stopped = _stopped(store, hooks)
  ended = run_attempt(store, ITEM, hooks, go_on="never")
  old = store.get(stopped.id)
  assert old is not None
  assert (old.state, old.outcome, old.reason) == (
    "ended",
    hooks.given_up,
    f"Started again as a new attempt ({ended.id})",
  )
  assert not hooks.hook_calls("take_over")
  assert f"cleanup {stopped.id}" in hooks.hook_calls("cleanup")
  last = store.events(stopped.id)[-1]
  assert (last.from_state, last.to_state, last.by) == (
    "ended",
    "ended",
    "runner",
  )


@pytest.mark.parametrize(
  ("stop", "canceled", "word"),
  [
    (KeyboardInterrupt, "canceled", "canceled"),
    (SystemExit, "canceled", "given_up"),
    (KeyboardInterrupt, None, "given_up"),
  ],
)
def test_a_row_a_person_stopped_is_given_up_with_the_datasets_canceled(
  store: Store,
  hooks: FakeHooks,
  monkeypatch: pytest.MonkeyPatch,
  stop: type[BaseException],
  canceled: str | None,
  word: str,
) -> None:
  # SIGINT stops a row as a person's stop, SIGTERM with no queue job as lost.
  def stopped(*_args: object) -> object:
    raise stop

  with monkeypatch.context() as patched:
    patched.setattr(FakeHooks, "set_up", stopped)
    with pytest.raises(stop):
      _ = run_attempt(store, ITEM, hooks)
  old = _only(store)
  hooks.canceled = canceled
  ended = run_attempt(store, ITEM, hooks, go_on="never")
  given_up = store.get(old.id)
  assert given_up is not None
  assert (given_up.state, given_up.outcome, given_up.reason) == (
    "ended",
    word,
    f"Started again as a new attempt ({ended.id})",
  )
  assert given_up.stopped_by == old.stopped_by


def test_an_on_ended_that_raises_for_a_given_up_row_leaves_the_new_attempt_be(
  store: Store, hooks: FakeHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
  stopped = _stopped(store, hooks)
  hooks.fail_in = "on_ended"
  # The reconcile after the end would settle the row at once.
  calls = 0

  def reconcile_once(
    store: Store, hooks: FakeHooks, recheck: Sequence[str] | None = None
  ) -> list[str]:
    nonlocal calls
    calls += 1
    return reconcile(store, hooks, recheck) if calls == 1 else []

  monkeypatch.setattr(runner, "reconcile", reconcile_once)
  ended = run_attempt(store, ITEM, hooks, go_on="never")
  assert (ended.state, ended.outcome) == ("ended", "passed")
  old = store.get(stopped.id)
  assert old is not None
  assert (old.state, old.outcome, old.settled_at) == (
    "ended",
    hooks.given_up,
    None,
  )
  assert f"cleanup {stopped.id}" in hooks.hook_calls("cleanup")
  _ = reconcile(store, hooks)
  old = store.get(stopped.id)
  assert old is not None
  assert old.settled_at is not None
  assert hooks.hook_calls("on_ended").count(f"on_ended {stopped.id}") == 2


def test_an_exit_in_on_ended_for_a_given_up_row_is_not_swallowed(
  store: Store, hooks: FakeHooks
) -> None:
  stopped = _stopped(store, hooks)
  hooks.stop_in = "on_ended"
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks, go_on="never")
  old = store.get(stopped.id)
  assert old is not None
  assert (old.state, old.settled_at) == ("ended", None)


def test_an_attempt_does_not_start_while_its_item_runs(
  store: Store, hooks: FakeHooks, me: Leader
) -> None:
  assert store.insert(f"{ITEM}/a", ITEM, Path("/r"), me, None)
  with pytest.raises(StartError, match=f"{ITEM}/a runs"):
    _ = run_attempt(store, ITEM, hooks)
  assert not hooks.calls()


def test_of_two_that_go_on_with_one_row_one_takes_it_over(
  store: Store,
  hooks: FakeHooks,
  barrier: Callable[[], None],
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  stopped = _stopped(store, hooks)
  monkeypatch.setattr(Store, "take", after(barrier, Store.take))

  def go_on() -> None:
    _ = run_attempt(store, ITEM, hooks, go_on="only")

  assert sorted(at_once(go_on, go_on)) == [0, 1]
  assert hooks.hook_calls("take_over") == [f"take_over {stopped.id}"]
  row = _only(store)
  assert (row.state, row.outcome) == ("ended", "passed")


def test_an_interrupt_in_finalize_is_ended_by_reconcile_whole(
  store: Store, hooks: FakeHooks
) -> None:
  hooks.stop_in = "finalize"
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks)
  row = _only(store)
  assert (row.state, row.machine_status) == ("interrupted", "passed")
  root = Path(row.root)
  assert not (root / KEPT).exists()
  _ = reconcile(store, hooks)
  row = _only(store)
  assert (row.state, row.outcome) == ("ended", "passed")
  assert (root / KEPT).read_text() == WHOLE
  assert row.settled_at is not None
  assert [(e.to_state, e.by) for e in store.events(row.id)][-4:] == [
    ("interrupted", "runner"),
    ("running", "reconcile"),
    ("ended", "reconcile"),
    ("ended", "reconcile"),
  ]
  assert hooks.hook_calls("cleanup") == [f"cleanup {row.id}"]


def test_an_interrupt_before_on_ended_returns_is_settled_by_reconcile(
  store: Store, hooks: FakeHooks
) -> None:
  hooks.stop_in = "on_ended"
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks)
  row = _only(store)
  assert (row.state, row.outcome, row.settled_at) == ("ended", "passed", None)
  assert not hooks.hook_calls("on_stop")
  _ = reconcile(store, hooks)
  row = _only(store)
  assert row.settled_at is not None
  assert len(hooks.hook_calls("on_ended")) == 2


def test_a_step_not_written_is_only_logged_and_the_attempt_goes_on(
  store: Store,
  hooks: FakeHooks,
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
) -> None:
  def unwritten(*_args: object) -> bool:
    raise sqlite3.OperationalError("disk I/O error")

  monkeypatch.setattr(Store, "step", unwritten)
  with caplog.at_level(logging.WARNING, logger=runner.__name__):
    ended = run_attempt(store, ITEM, hooks)
  assert (ended.state, ended.outcome) == ("ended", "passed")
  assert "the step was not written: disk I/O error" in caplog.text


def test_an_end_not_written_exits_75_and_reconcile_ends_it_from_the_file(
  store: Store, hooks: FakeHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setattr(runner, "END_RETRIES_S", (0.0, 0.0, 0.0))

  def unwritten(*_args: object, **_kwargs: object) -> bool:
    raise sqlite3.OperationalError("disk I/O error")

  def attempt() -> None:
    monkeypatch.setattr(Store, "step", unwritten)
    monkeypatch.setattr(Store, "end", unwritten)
    _ = run_attempt(store, ITEM, hooks)

  assert at_once(attempt) == [EXIT_UNWRITTEN]
  monkeypatch.undo()
  row = _only(store)
  # Neither a step nor the end reached the store; the record file has both.
  assert (row.state, row.record, row.machine_status) == ("running", None, None)
  done = reconcile(store, hooks)
  row = _only(store)
  assert (row.state, row.outcome, row.machine_status) == (
    "ended",
    "passed",
    "passed",
  )
  assert len(visits(row.record)) == 1
  # The record, without what the rendering of the record file added.
  assert row.record is not None
  assert "machine_status" not in row.record
  assert f"{row.id}: interrupted -> ended, passed" in done


def test_an_end_is_written_on_a_retry(
  store: Store, hooks: FakeHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setattr(runner, "END_RETRIES_S", (0.0, 0.0, 0.0))
  errors: list[Exception] = [sqlite3.OperationalError("locked")] * 3
  monkeypatch.setattr(Store, "end", failing(errors, Store.end))
  ended = run_attempt(store, ITEM, hooks)
  assert (ended.state, ended.outcome) == ("ended", "passed")


def test_the_queue_job_is_written_into_the_row(
  store: Store,
  hooks: FakeHooks,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  db = queue_db(tmp_path / "q.sqlite", {1: False})
  monkeypatch.setenv(JOB_ID_ENV, "1")
  monkeypatch.setenv(QUEUE_DB_ENV, str(db))
  ended = run_attempt(store, ITEM, hooks)
  assert (ended.job_id, ended.job_db) == (1, str(db))


@pytest.mark.parametrize(
  ("machine", "reason"),
  [("all trials passed", "work visit 1: all trials passed"), (None, "mine")],
)
def test_the_row_takes_the_machines_reason_over_the_datasets(
  store: Store,
  hooks: FakeHooks,
  monkeypatch: pytest.MonkeyPatch,
  machine: str | None,
  reason: str,
) -> None:
  def done(*_args: object) -> Outcome:
    return Outcome("done", reason=machine)

  def outcome(*_args: object) -> tuple[str, str | None]:
    return "passed", "mine"

  monkeypatch.setattr(Work, "run", done)
  monkeypatch.setattr(FakeHooks, "outcome", outcome)
  ended = run_attempt(store, ITEM, hooks)
  assert (ended.outcome, ended.reason) == ("passed", reason)


def test_a_sigterm_as_a_steps_commit_returns_stops_the_attempt(
  store: Store, hooks: FakeHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
  # The signal arrives while a step's COMMIT runs: its handler raises once
  # the call returns, with no transaction left to roll back.
  connect = sqlite3.connect
  armed: list[bool] = []

  class Signalled(sqlite3.Connection):
    # typeshed types the parameters with an alias of its own, private.
    @override
    def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
      self, sql: str, parameters: Sequence[object] = (), /
    ) -> sqlite3.Cursor:
      cursor = super().execute(sql, parameters)
      if sql == "COMMIT" and armed == [True]:
        armed.append(True)
        os.kill(os.getpid(), signal.SIGTERM)
      return cursor

  def connecting(
    database: str | Path,
    *,
    timeout: float = 5.0,
    uri: bool = False,
    autocommit: bool = False,
  ) -> sqlite3.Connection:
    return connect(
      database, timeout=timeout, uri=uri, autocommit=autocommit,
      factory=Signalled,
    )  # fmt: skip

  def arming(
    self: Store, attempt_id: str, holder: Leader, *more: object
  ) -> bool:
    if not armed:
      armed.append(True)
    return step(self, attempt_id, holder, *more)  # pyright: ignore[reportArgumentType]

  step = Store.step
  monkeypatch.setattr(sqlite3, "connect", connecting)
  monkeypatch.setattr(Store, "step", arming)
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks)
  monkeypatch.undo()
  row = _only(store)
  assert (row.state, row.stopped_by) == ("interrupted", "lost")
  [on_stop] = hooks.hook_calls("on_stop")
  assert on_stop.split()[-1] == "interrupted"
  assert not hooks.hook_calls("finalize")


def test_a_row_whose_machine_ended_is_never_given_up(
  store: Store, hooks: FakeHooks
) -> None:
  # Its finalize was cut, and fails once more when the next attempt starts.
  hooks.stop_in = "finalize"
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks)
  [stopped] = store.rows()
  hooks.fail_in = "finalize"
  ended = run_attempt(store, ITEM, hooks, go_on="never")
  old = store.get(stopped.id)
  assert old is not None
  assert (old.state, old.outcome, old.machine_status) == (
    "ended",
    "passed",
    "passed",
  )
  assert ended.id != stopped.id
  assert (Path(old.root) / KEPT).read_text() == WHOLE


def test_an_end_its_row_refuses_exits_75_at_once(
  store: Store, hooks: FakeHooks, monkeypatch: pytest.MonkeyPatch
) -> None:
  waits: list[float] = []

  def refused(*_args: object, **_kwargs: object) -> bool:
    return False

  monkeypatch.setattr(time, "sleep", waits.append)
  monkeypatch.setattr(Store, "end", refused)
  with pytest.raises(SystemExit) as stop:
    _ = run_attempt(store, ITEM, hooks)
  assert (stop.value.code, waits) == (EXIT_UNWRITTEN, [])

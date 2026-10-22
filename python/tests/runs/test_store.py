from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from dataforge.queue.proc import Leader
from dataforge.runs import (
  AttemptRow,
  NewerSchemaError,
  QueueJob,
  reconcile,
  SCHEMA_MAJOR,
  SCHEMA_MINOR,
  Store,
)
from tests.runs import schema_1_0
from tests.runs.conftest import after, at_once

ITEM = "item-1"
RECORD: dict[str, object] = {
  "status": None,
  "visits": [],
  "budget": {"cost_usd": 0.5},
}
ENDED: dict[str, object] = {
  "status": "passed",
  "visits": [{}],
  "budget": {"cost_usd": 2.0},
}


def _in(store: Store, state: str, holder: Leader, item: str = ITEM) -> str:
  # A row of the item in the state, held by holder.
  attempt_id = f"{item}/2026-01-01T000000Z"
  assert store.insert(attempt_id, item, Path("/r"), holder, None)
  if state == "interrupted":
    assert store.interrupt(attempt_id, holder, by="runner", stopped_by="lost")
  elif state == "ended":
    assert store.end(
      attempt_id,
      holder,
      by="runner",
      outcome="passed",
      reason=None,
      record=ENDED,
      cost_usd=2.0,
    )
  return attempt_id


def _states(store: Store) -> list[tuple[str | None, str, str]]:
  [row] = store.rows()
  return [(e.from_state, e.to_state, e.by) for e in store.events(row.id)]


def test_insert_makes_a_running_row_held_by_its_process(
  store: Store, me: Leader
) -> None:
  job = QueueJob(7, "/q.sqlite")
  assert store.insert(f"{ITEM}/a", ITEM, Path("/r/a"), me, job)
  [row] = store.rows()
  assert (row.id, row.item, row.root, row.state) == (
    f"{ITEM}/a",
    ITEM,
    "/r/a",
    "running",
  )
  assert (row.holder, row.job_id, row.job_db) == (me, 7, "/q.sqlite")
  assert (row.outcome, row.record, row.resumable, row.extra) == (
    None,
    None,
    None,
    {},
  )
  assert _states(store) == [(None, "running", "runner")]


def test_a_step_writes_the_record_of_a_running_row(
  store: Store, me: Leader
) -> None:
  attempt_id = _in(store, "running", me)
  assert store.step(attempt_id, me, RECORD, 0.5)
  row = store.get(attempt_id)
  assert row is not None
  assert (row.state, row.record, row.cost_usd) == ("running", RECORD, 0.5)


def test_end_writes_the_outcome_of_a_running_row(
  store: Store, me: Leader
) -> None:
  attempt_id = _in(store, "running", me)
  assert store.end(
    attempt_id,
    me,
    by="runner",
    outcome="kept",
    reason="why",
    record=ENDED,
    cost_usd=2.0,
  )
  row = store.get(attempt_id)
  assert row is not None
  assert (row.state, row.outcome, row.reason, row.machine_status) == (
    "ended",
    "kept",
    "why",
    "passed",
  )
  assert row.ended_at is not None
  assert _states(store)[-1] == ("running", "ended", "runner")


def test_interrupt_makes_a_running_row_interrupted(
  store: Store, me: Leader
) -> None:
  attempt_id = _in(store, "running", me)
  assert store.interrupt(
    attempt_id, me, by="runner", stopped_by="person", problem="gone"
  )
  row = store.get(attempt_id)
  assert row is not None
  assert (row.state, row.stopped_by, row.resumable, row.resume_problem) == (
    "interrupted",
    "person",
    False,
    "gone",
  )
  assert _states(store)[-1] == ("running", "interrupted", "runner")


def test_take_makes_an_interrupted_row_running_under_its_new_holder(
  store: Store, me: Leader, gone: Leader
) -> None:
  attempt_id = _in(store, "interrupted", gone)
  assert store.check(attempt_id, None, 1.0)
  before = store.get(attempt_id)
  assert before is not None
  assert store.take(attempt_id, me, QueueJob(3, "/q"), by="runner")
  row = store.get(attempt_id)
  assert row is not None
  assert (row.state, row.holder, row.stopped_by, row.job_id) == (
    "running",
    me,
    None,
    3,
  )
  assert (row.resumable, row.resume_problem, row.resume_checked_at) == (
    None,
    None,
    None,
  )
  assert row.progress_at is not None
  assert before.progress_at is not None
  assert row.progress_at > before.progress_at
  assert _states(store)[-1] == ("interrupted", "running", "runner")


def test_abandon_ends_an_interrupted_row(store: Store, me: Leader) -> None:
  attempt_id = _in(store, "interrupted", me)
  assert store.abandon(attempt_id, "given_up", "b took its place")
  row = store.get(attempt_id)
  assert row is not None
  assert (row.state, row.outcome, row.reason) == (
    "ended",
    "given_up",
    "b took its place",
  )
  assert _states(store)[-1] == ("interrupted", "ended", "runner")


def test_abandon_leaves_a_row_whose_machine_ended(
  store: Store, me: Leader
) -> None:
  attempt_id = _in(store, "interrupted", me)
  assert store.newer_record(attempt_id, ENDED)
  before = store.get(attempt_id)
  assert not store.abandon(attempt_id, "given_up", "b took its place")
  assert store.get(attempt_id) == before


def test_check_writes_resumable_and_cost_of_an_interrupted_row(
  store: Store, me: Leader
) -> None:
  attempt_id = _in(store, "interrupted", me)
  assert store.check(attempt_id, None, 3.25)
  row = store.get(attempt_id)
  assert row is not None
  assert (row.resumable, row.resume_problem, row.cost_usd) == (True, None, 3.25)
  assert row.resume_checked_at is not None
  assert store.check(attempt_id, "its container is gone", None)
  row = store.get(attempt_id)
  assert row is not None
  assert (row.resumable, row.resume_problem, row.cost_usd) == (
    False,
    "its container is gone",
    3.25,
  )


def test_newer_record_writes_into_an_interrupted_row(
  store: Store, me: Leader
) -> None:
  attempt_id = _in(store, "interrupted", me)
  assert store.newer_record(attempt_id, ENDED)
  row = store.get(attempt_id)
  assert row is not None
  assert (row.record, row.machine_status) == (ENDED, "passed")


def test_settle_marks_an_ended_row_once(store: Store, me: Leader) -> None:
  attempt_id = _in(store, "ended", me)
  assert store.settle(attempt_id)
  row = store.get(attempt_id)
  assert row is not None
  assert row.settled_at is not None
  assert not store.settle(attempt_id)


def test_cleaned_adds_an_event_and_changes_no_row(
  store: Store, me: Leader
) -> None:
  attempt_id = _in(store, "ended", me)
  before = store.get(attempt_id)
  assert store.cleaned(
    attempt_id, {"containers": 1}, "TimeoutExpired: docker", by="runner"
  )
  assert store.get(attempt_id) == before
  events = store.events(attempt_id)
  last = events[-1]
  assert (last.from_state, last.to_state, last.detail) == (
    "ended",
    "ended",
    {"cleanup_left": {"containers": 1}, "raised": "TimeoutExpired: docker"},
  )
  # A second cleanup, such as one of two reconciles, adds no event.
  assert not store.cleaned(attempt_id, {}, None, by="reconcile")
  assert store.events(attempt_id) == events


def _kept(state: str, holder: Leader) -> AttemptRow:
  # A row as a dataset kept it before the store, every field set.
  ended = state == "ended"
  return AttemptRow(
    id=f"{ITEM}/2025-01-01T000000Z",
    item=ITEM,
    root="/r",
    state=state,
    outcome="passed" if ended else None,
    reason="reviewed" if ended else None,
    machine_status="passed" if ended else None,
    stopped_by=None if ended else "person",
    record=ENDED if ended else RECORD,
    cost_usd=2.0,
    progress_at="2025-01-01T01:00:00+00:00",
    holder=holder,
    job_id=7,
    job_db="/q.sqlite",
    resumable=None if ended else True,
    resume_problem=None,
    resume_checked_at=None if ended else "2025-01-01T02:00:00+00:00",
    created_at="2025-01-01T00:00:00+00:00",
    ended_at="2025-01-01T03:00:00+00:00" if ended else None,
    settled_at="2025-01-01T03:00:01+00:00" if ended else None,
    extra={"stage": "ab"},
  )


@pytest.mark.parametrize("state", ["ended", "interrupted"])
def test_add_keeps_a_row_as_it_is_given_with_no_holder(
  store: Store, gone: Leader, state: str
) -> None:
  row = _kept(state, gone)
  assert store.add(row)
  assert store.get(row.id) == replace(row, holder=None)
  assert _states(store) == [(None, state, "add")]


def test_an_added_interrupted_row_is_taken_as_any_other(
  store: Store, me: Leader
) -> None:
  row = _kept("interrupted", me)
  assert store.add(row)
  assert store.take(row.id, me, None, by="runner")
  taken = store.get(row.id)
  assert taken is not None
  assert (taken.state, taken.holder) == ("running", me)


def test_add_of_a_taken_id_changes_nothing(store: Store, me: Leader) -> None:
  row = _kept("ended", me)
  assert store.add(row)
  before, events = store.get(row.id), store.events(row.id)
  assert not store.add(replace(row, outcome="failed"))
  assert store.get(row.id) == before
  assert store.events(row.id) == events


@pytest.mark.parametrize(
  ("state", "change"),
  [
    ("ended", {"state": "running"}),
    ("ended", {"outcome": None}),
    ("ended", {"ended_at": None}),
    ("interrupted", {"stopped_by": None}),
    ("interrupted", {"stopped_by": "nobody"}),
    ("interrupted", {"outcome": "passed"}),
    ("interrupted", {"ended_at": "2025-01-01T03:00:00+00:00"}),
    ("interrupted", {"settled_at": "2025-01-01T03:00:01+00:00"}),
  ],
)
def test_add_refuses_a_running_row_or_one_its_fields_contradict(
  store: Store, me: Leader, state: str, change: dict[str, str | None]
) -> None:
  row = replace(_kept(state, me), **change)
  with pytest.raises(ValueError, match=row.id):
    _ = store.add(row)
  assert store.rows() == []


type Write = Callable[[Store, str, Leader], bool]

WRITES: dict[str, Write] = {
  "step": lambda s, i, h: s.step(i, h, RECORD, 0.5),
  "end": lambda s, i, h: s.end(
    i, h, by="runner", outcome="x", reason=None, record=ENDED, cost_usd=1.0
  ),
  "interrupt": lambda s, i, h: s.interrupt(
    i, h, by="runner", stopped_by="lost"
  ),
  "take": lambda s, i, h: s.take(i, h, None, by="runner"),
  "abandon": lambda s, i, _h: s.abandon(i, "given_up", "why"),
  "check": lambda s, i, _h: s.check(i, None, 1.0),
  "newer_record": lambda s, i, _h: s.newer_record(i, ENDED),
  "settle": lambda s, i, _h: s.settle(i),
}

LEGAL = {
  "step": "running",
  "end": "running",
  "interrupt": "running",
  "take": "interrupted",
  "abandon": "interrupted",
  "check": "interrupted",
  "newer_record": "interrupted",
  "settle": "ended",
}

ILLEGAL = [
  (write, state)
  for write, legal in LEGAL.items()
  for state in ("running", "interrupted", "ended")
  if state != legal
]


@pytest.mark.parametrize(("write", "state"), ILLEGAL)
def test_a_write_on_a_row_in_another_state_changes_nothing(
  store: Store, me: Leader, write: str, state: str
) -> None:
  attempt_id = _in(store, state, me)
  before, events = store.get(attempt_id), store.events(attempt_id)
  assert not WRITES[write](store, attempt_id, me)
  assert store.get(attempt_id) == before
  assert store.events(attempt_id) == events


@pytest.mark.parametrize("write", ["step", "end", "interrupt"])
def test_a_write_on_a_running_row_by_another_holder_changes_nothing(
  store: Store, me: Leader, gone: Leader, write: str
) -> None:
  attempt_id = _in(store, "running", me)
  before = store.get(attempt_id)
  assert not WRITES[write](store, attempt_id, gone)
  assert store.get(attempt_id) == before


def test_an_item_has_one_running_row(
  store: Store, me: Leader, gone: Leader
) -> None:
  _ = _in(store, "interrupted", gone)
  assert store.insert(f"{ITEM}/b", ITEM, Path("/r/b"), me, None)
  # Neither a second insert nor a take of the interrupted row runs it twice.
  assert not store.insert(f"{ITEM}/c", ITEM, Path("/r/c"), me, None)
  assert not store.take(f"{ITEM}/2026-01-01T000000Z", me, None, by="runner")
  assert [row.state for row in store.rows()] == ["interrupted", "running"]


def test_of_two_processes_that_insert_one_item_at_once_one_runs_it(
  store: Store,
  me: Leader,
  barrier: Callable[[], None],
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  store.create()
  monkeypatch.setattr(Store, "insert", after(barrier, Store.insert))

  def start(name: str) -> Callable[[], None]:
    def run() -> None:
      if not store.insert(f"{ITEM}/{name}", ITEM, Path("/r"), me, None):
        raise SystemExit(1)

    return run

  assert sorted(at_once(start("a"), start("b"))) == [0, 1]
  assert [row.state for row in store.rows()] == ["running"]


def test_a_writer_of_schema_1_0_runs_every_transition_on_this_schema(
  store: Store, me: Leader, gone: Leader
) -> None:
  # The statements as dataforge wrote them at schema 1.0, kept in the test,
  # so that a column added since without a default fails them.
  store.create()
  with closing(sqlite3.connect(store.path, autocommit=True)) as conn:
    schema_1_0.transitions(conn, me, gone)
  rows = {row.id: row for row in store.rows()}
  assert {i: row.state for i, row in rows.items()} == {
    "a/1": "ended",
    "b/1": "ended",
    "c/1": "interrupted",
    "d/1": "ended",
  }
  assert (rows["a/1"].outcome, rows["a/1"].settled_at is not None) == (
    "passed",
    True,
  )
  assert rows["a/1"].record == {"status": "passed", "visits": []}
  assert rows["b/1"].outcome == "given_up"
  assert (rows["c/1"].resumable, rows["c/1"].cost_usd) == (False, 1.0)
  assert rows["a/1"].extra == {}
  assert (rows["d/1"].extra, rows["d/1"].settled_at) == (
    {"stage": "ab"},
    schema_1_0.AT,
  )
  assert [(e.to_state, e.by) for e in store.events("d/1")] == [("ended", "add")]
  assert [e.to_state for e in store.events("a/1")] == [
    "running",
    "interrupted",
    "running",
    "ended",
  ]


def test_a_new_store_has_this_schema(store: Store) -> None:
  store.create()
  assert store.schema() == (SCHEMA_MAJOR, SCHEMA_MINOR)


def test_a_store_of_a_newer_schema_major_is_read_and_never_written(
  store: Store, me: Leader, gone: Leader
) -> None:
  held = _in(store, "running", gone)
  waiting = _in(store, "interrupted", me, item="item-2")
  with closing(sqlite3.connect(store.path, autocommit=True)) as conn:
    _ = conn.execute(
      "UPDATE meta SET value = ? WHERE name = 'schema_major'",
      (SCHEMA_MAJOR + 1,),
    )
  before = store.rows()
  with pytest.raises(NewerSchemaError):
    _ = store.insert("item-3/a", "item-3", Path("/r"), me, None)
  for write, state in LEGAL.items():
    attempt_id = held if state == "running" else waiting
    holder = gone if state == "running" else me
    with pytest.raises(NewerSchemaError):
      _ = WRITES[write](store, attempt_id, holder)
  with pytest.raises(NewerSchemaError):
    _ = store.add(_kept("ended", me))
  with pytest.raises(NewerSchemaError):
    _ = reconcile(store)
  assert store.rows() == before

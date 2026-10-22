from collections.abc import Callable
import json
from pathlib import Path
from typing import cast

import pytest

from dataforge.queue.proc import Leader
from dataforge.runs import QueueJob, reconcile, run_attempt, Store
from tests.runs.conftest import after, at_once, queue_db, visits
from tests.runs.fake import FakeHooks, KEPT, WHOLE

ITEM = "item-1"


def test_a_row_whose_holder_is_gone_is_interrupted_as_its_job_says(
  store: Store, gone: Leader, tmp_path: Path
) -> None:
  db = str(queue_db(tmp_path / "q.sqlite", {1: True, 2: False}))
  for item, job in (
    ("a", QueueJob(1, db)),
    ("b", QueueJob(2, db)),
    ("c", None),
  ):
    assert store.insert(f"{item}/1", item, Path("/r"), gone, job)
  done = reconcile(store)
  rows = {row.item: row for row in store.rows()}
  assert {item: (row.state, row.stopped_by) for item, row in rows.items()} == {
    "a": ("interrupted", "person"),
    "b": ("interrupted", "lost"),
    "c": ("interrupted", "lost"),
  }
  assert done == [
    "a/1: running -> interrupted, person",
    "b/1: running -> interrupted, lost",
    "c/1: running -> interrupted, lost",
  ]
  assert [e.by for e in store.events("a/1")] == ["runner", "reconcile"]
  # The rows were not checked: no dataset code ran.
  assert {row.resumable for row in rows.values()} == {None}


def test_a_row_whose_holder_runs_is_left_running(
  store: Store, me: Leader
) -> None:
  assert store.insert("a/1", "a", Path("/r"), me, None)
  assert reconcile(store) == []
  assert [row.state for row in store.rows()] == ["running"]


def test_a_row_its_holder_ended_is_never_changed(
  store: Store, hooks: FakeHooks, gone: Leader
) -> None:
  assert store.insert("a/1", "a", Path("/r"), gone, None)
  assert store.end(
    "a/1",
    gone,
    by="runner",
    outcome="passed",
    reason=None,
    record={"status": "passed", "visits": []},
    cost_usd=1.0,
  )
  assert store.settle("a/1")
  before, events = store.rows(), store.events("a/1")
  assert reconcile(store) == []
  assert reconcile(store, hooks) == []
  assert store.rows() == before
  assert store.events("a/1") == events
  assert not hooks.calls()


def test_without_hooks_an_interrupted_row_whose_machine_ended_stays(
  store: Store, hooks: FakeHooks
) -> None:
  hooks.stop_in = "finalize"
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks)
  before = store.rows()
  assert reconcile(store) == []
  assert store.rows() == before


def test_of_two_that_reconcile_a_row_with_hooks_at_once_one_finalizes(
  store: Store,
  hooks: FakeHooks,
  barrier: Callable[[], None],
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  hooks.stop_in = "finalize"
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks)
  [row] = store.rows()
  assert (row.state, row.machine_status) == ("interrupted", "passed")
  finalized = len(hooks.hook_calls("finalize"))
  monkeypatch.setattr(Store, "take", after(barrier, Store.take))

  def again() -> None:
    _ = reconcile(store, hooks)

  assert at_once(again, again) == [0, 0]
  assert len(hooks.hook_calls("finalize")) == finalized + 1
  [row] = store.rows()
  assert (row.state, row.outcome) == ("ended", "passed")
  assert (Path(row.root) / KEPT).read_text() == WHOLE


def test_a_record_file_with_more_visits_is_written_into_the_row(
  store: Store, hooks: FakeHooks
) -> None:
  hooks.sigterm = True
  with pytest.raises(SystemExit):
    _ = run_attempt(store, ITEM, hooks)
  [row] = store.rows()
  assert row.record is not None
  file = Path(row.root) / hooks.record
  written = cast(dict[str, object], json.loads(file.read_text()))
  more = [*visits(written), visits(written)[-1]]
  _ = file.write_text(json.dumps(written | {"visits": more}))
  done = reconcile(store, hooks)
  [row] = store.rows()
  assert len(visits(row.record)) == len(more)
  assert (row.state, row.machine_status) == ("interrupted", None)
  assert f"{row.id}: the record file's newer record" in done


def test_only_the_items_in_recheck_are_checked_again(
  store: Store, hooks: FakeHooks, gone: Leader
) -> None:
  for item in ("a", "b"):
    assert store.insert(f"{item}/1", item, Path("/r"), gone, None)
  _ = reconcile(store)
  hooks.problem = "its container is gone"
  assert reconcile(store, hooks, recheck=["a"]) == [
    "a/1: resumable no, its container is gone"
  ]
  rows = {row.item: row for row in store.rows()}
  assert (rows["a"].resumable, rows["a"].cost_usd) == (False, 1.5)
  assert (rows["b"].resumable, rows["b"].cost_usd) == (None, None)

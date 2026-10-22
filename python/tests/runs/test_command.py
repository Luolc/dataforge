import json
from pathlib import Path
from typing import cast

import pytest

from dataforge import cli
from dataforge.queue.proc import Leader
from dataforge.runs import run_attempt, Store
from tests.runs import fake
from tests.runs.fake import FakeHooks

HOOKS = "tests.runs.fake:HOOKS"


@pytest.fixture
def module_hooks(
  hooks: FakeHooks, monkeypatch: pytest.MonkeyPatch
) -> FakeHooks:
  # The hooks --hooks names, set as the test's.
  monkeypatch.setattr(fake.HOOKS, "base", hooks.base)
  monkeypatch.setattr(fake.HOOKS, "store", hooks.store)
  return fake.HOOKS


def test_list_and_show_print_the_rows(
  store: Store, hooks: FakeHooks, capsys: pytest.CaptureFixture[str]
) -> None:
  ended = run_attempt(store, "a", hooks)
  assert cli.main(["runs", "list", str(store.path)]) == 0
  [line] = capsys.readouterr().out.splitlines()
  assert line.split("\t")[:5] == [ended.id, "ended", "passed", "passed", "-"]
  assert cli.main(["runs", "list", str(store.path), "--json"]) == 0
  [listed] = cast(list[dict[str, object]], json.loads(capsys.readouterr().out))
  assert (listed["id"], "record" in listed) == (ended.id, False)
  assert cli.main(["runs", "show", str(store.path), ended.id]) == 0
  shown = cast(dict[str, object], json.loads(capsys.readouterr().out))
  assert shown["record"] == ended.record
  events = cast(list[dict[str, object]], shown["events"])
  assert [e["to"] for e in events] == ["running", "ended", "ended"]


def test_list_filters_by_state_and_item(
  store: Store, gone: Leader, capsys: pytest.CaptureFixture[str]
) -> None:
  for item in ("a", "b"):
    assert store.insert(f"{item}/1", item, Path("/r"), gone, None)
  args = ["runs", "list", str(store.path), "--state", "running", "--item", "b"]
  assert cli.main(args) == 0
  assert [
    line.split("\t")[0] for line in capsys.readouterr().out.splitlines()
  ] == ["b/1"]


def test_reconcile_prints_each_change(
  store: Store, gone: Leader, capsys: pytest.CaptureFixture[str]
) -> None:
  assert store.insert("a/1", "a", Path("/r"), gone, None)
  assert cli.main(["runs", "reconcile", str(store.path)]) == 0
  assert capsys.readouterr().out == "a/1: running -> interrupted, lost\n"
  assert cli.main(["runs", "reconcile", str(store.path)]) == 0
  assert capsys.readouterr().out == "nothing to change\n"


def test_reconcile_with_hooks_ends_a_row_whose_machine_ended(
  store: Store,
  module_hooks: FakeHooks,
  capsys: pytest.CaptureFixture[str],
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(module_hooks, "stop_in", "finalize")
  with pytest.raises(SystemExit):
    _ = run_attempt(store, "a", module_hooks)
  [row] = store.rows()
  args = ["runs", "reconcile", str(store.path), "--hooks", HOOKS]
  assert cli.main(args) == 0
  assert capsys.readouterr().out.splitlines() == [
    f"{row.id}: interrupted -> ended, passed",
    f"{row.id}: settled",
    f"{row.id}: cleaned up",
  ]


def test_render_writes_the_record_file(
  store: Store, module_hooks: FakeHooks, capsys: pytest.CaptureFixture[str]
) -> None:
  ended = run_attempt(store, "a", module_hooks)
  file = Path(ended.root) / module_hooks.record
  file.unlink()
  args = ["runs", "render", str(store.path), ended.id, "--hooks", HOOKS]
  assert cli.main(args) == 0
  assert capsys.readouterr().out == f"{file}\n"
  written = cast(dict[str, object], json.loads(file.read_text()))
  assert (written["status"], written["visits"]) == (
    "passed",
    (ended.record or {})["visits"],
  )


@pytest.mark.parametrize(
  ("args", "code", "error"),
  [
    (["list", "{db}x"], 1, "no store"),
    (["show", "{db}", "a/none"], 1, "no attempt a/none"),
    (["reconcile", "{db}", "--hooks", "tests.runs.fake"], 2, "MODULE:NAME"),
    (["reconcile", "{db}", "--hooks", "tests.runs.fake:KEPT"], 2, "not a"),
  ],
)
def test_a_bad_call_says_why(
  store: Store,
  capsys: pytest.CaptureFixture[str],
  args: list[str],
  code: int,
  error: str,
) -> None:
  store.create()
  argv = [arg.format(db=store.path) for arg in args]
  assert cli.main(["runs", *argv]) == code
  assert error in capsys.readouterr().err

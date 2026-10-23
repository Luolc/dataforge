import json
from pathlib import Path
import shutil
from typing import cast

import pytest

from dataforge.cli import main
from dataforge.curation import Attempt, Deliverable, End, Machine, Source, Value
from dataforge.curation.redeliver import describe, redeliver
from tests.test_engine import regrade, Step

STATES = [Step("regrade", regrade, {"done": End("passed")})]
OLD = {
  "regrade": Deliverable(
    "trials", "Regrade", Source("regrade"), value=Value(["solved"])
  )
}
NEW = {
  "regrade": Deliverable(
    "trials",
    "Regrade",
    Source("regrade"),
    value=Value(["solved"]),
    match="regrade-[0-9]*",
  )
}
NOW_DECLARED = """\
dataforge: 1
name: demo
start: regrade
states:
  regrade:
    uses: script
    with: {argv: ["true"]}
    transitions: {ok: {end: passed}, failed: {end: failed}}
deliverables:
  regrade:
    kind: trials
    title: Regrade
    source: {state: regrade}
    match: "regrade-[0-9]*"
    value: {field: [solved]}
"""


def ended_record(root: Path, status: object = "passed") -> Path:
  """Run an attempt under the old deliverables and write its record.

  The record holds a key of the dataset's own, and the dataset's status in
  place of the engine's, as a dataset may write it.

  Args:
    root: The attempt's directory.
    status: The status the dataset writes.

  Returns:
    The record file.
  """
  root.mkdir()
  record = root / "record.json"

  def write(attempt: Attempt) -> None:
    data = {"item": "item-1", **attempt.to_json(), "status": status}
    _ = record.write_text(json.dumps(data, indent=2) + "\n")

  _ = Machine(STATES, deliverables=OLD, on_step=write).run(root)
  return record


def read(record: Path) -> dict[str, object]:
  return cast(dict[str, object], json.loads(record.read_bytes()))


def entries(data: dict[str, object]) -> list[dict[str, object]]:
  return cast(list[dict[str, object]], data["deliverables"])


def test_redeliver_looks_first_and_writes_only_the_deliverables(
  tmp_path: Path,
) -> None:
  record = ended_record(tmp_path / "attempt")
  before = record.read_bytes()
  old = read(record)
  assert entries(old)[0]["k"] == 5
  machine = Machine(STATES, deliverables=NEW)

  looked = redeliver(machine, record)
  assert looked.action == "change"
  assert record.read_bytes() == before
  assert describe(looked)[1:] == [
    (
      "  ~ regrade: trials 'Regrade', artifacts/regrade/1, 1 / 5"
      " -> trials 'Regrade', artifacts/regrade/1, 1 / 3"
    )
  ]

  wrote = redeliver(machine, record, write=True)
  assert wrote.action == "wrote"
  assert wrote.backup is not None
  assert wrote.backup.parent == record.parent
  assert wrote.backup.read_bytes() == before
  after = read(record)
  assert list(after) == list(old)
  assert {k: v for k, v in after.items() if k != "deliverables"} == {
    k: v for k, v in old.items() if k != "deliverables"
  }
  [entry] = entries(after)
  assert (entry["match"], entry["k"]) == ("regrade-[0-9]*", 3)
  assert record.read_text().startswith('{\n  "item": "item-1",\n')
  assert redeliver(machine, record).action == "same"

  _ = shutil.move(wrote.backup, record)
  assert record.read_bytes() == before


@pytest.mark.parametrize(
  ("status", "not_ended", "outcome", "why"),
  [
    (
      "running",
      ["running"],
      "done",
      "its status 'running' is a word of an attempt not ended",
    ),
    (
      "passed",
      None,
      None,
      "its last visit has no outcome, so it has not ended",
    ),
    (
      None,
      None,
      "done",
      "its status is null and its last visit did not raise",
    ),
  ],
)
def test_an_attempt_that_has_not_ended_is_never_written(
  tmp_path: Path,
  status: str | None,
  not_ended: list[str] | None,
  outcome: str | None,
  why: str,
) -> None:
  record = ended_record(tmp_path / "attempt", status)
  data = read(record)
  cast(list[dict[str, object]], data["visits"])[-1]["outcome"] = outcome
  _ = record.write_text(json.dumps(data, indent=2) + "\n")
  before = record.read_bytes()
  machine = Machine(STATES, deliverables=NEW)
  result = redeliver(machine, record, not_ended=not_ended, write=True)
  assert (result.action, result.why) == ("skip", why)
  assert record.read_bytes() == before
  assert sorted(p.name for p in record.parent.iterdir()) == [
    "artifacts",
    "record.json",
  ]


@pytest.mark.parametrize("ending", ["raised", "interrupted"])
def test_a_record_whose_last_visit_did_not_finish_gets_what_those_before_made(
  tmp_path: Path, ending: str
) -> None:
  # As a dataforge before the visits before it were kept wrote it: a second
  # visit that did not finish, and no deliverables.
  record = ended_record(tmp_path / "attempt", None)
  data = read(record)
  visits = cast(list[dict[str, object]], data["visits"])
  ending_visit: dict[str, object] = {
    "visit": 2,
    "outcome": ending,
    "actions": [],
  }
  visits.append(visits[0] | ending_visit)
  data["deliverables"] = []
  _ = record.write_text(json.dumps(data, indent=2) + "\n")
  machine = Machine(STATES, deliverables=NEW)

  wrote = redeliver(machine, record, write=True)

  assert wrote.action == "wrote"
  [entry] = entries(read(record))
  assert (entry["visit_index"], entry["path"], entry["k"]) == (
    0,
    "artifacts/regrade/1",
    3,
  )


def test_dataforge_redeliver_previews_then_writes(
  tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  declaration = tmp_path / "curate.yaml"
  _ = declaration.write_text(NOW_DECLARED)
  done = ended_record(tmp_path / "done")
  running = ended_record(tmp_path / "running", "running")
  before = done.read_bytes()
  args = ["redeliver", str(declaration), str(done), str(running)]
  args += ["--not-ended", "running"]
  assert main(args) == 0
  out = capsys.readouterr().out.splitlines()
  assert out[0] == f"change {done}"
  assert out[2] == (
    f"skip {running}: its status 'running' is a word of an attempt not ended"
  )
  assert out[3] == "2 records: change 1, same 0, skip 1, error 0"
  assert done.read_bytes() == before

  assert main([*args, "--write"]) == 0
  out = capsys.readouterr().out.splitlines()
  assert out[0].startswith(f"wrote {done}")
  assert out[-1] == "2 records: wrote 1, same 0, skip 1, error 0"
  assert entries(read(done))[0]["k"] == 3

  # A record that cannot be read fails the command, not the others.
  missing = tmp_path / "missing" / "record.json"
  assert main(["redeliver", str(declaration), str(missing), str(done)]) == 1
  out = capsys.readouterr().out.splitlines()
  assert out[0].startswith(f"error {missing}: cannot read")
  assert out[1:] == [
    f"same {done}",
    "2 records: change 0, same 1, skip 0, error 1",
  ]

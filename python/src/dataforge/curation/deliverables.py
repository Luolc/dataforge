"""What an attempt delivers: declared by name, resolved as it runs and ends.

A dataset declares each deliverable as a Deliverable: its kind, which says how
it is shown ("files", "trials" or "report"), a title, and where it comes from,
a state's output in one of the state's visits, or the output of one of several
states, in the last of their visits that wrote it. The machine
(dataforge.curation.engine) resolves them into the record's "deliverables" at
each step, as the attempt would end then, and when it ends, with paths
relative to the attempt's root and, for what was not reached, a reason instead
of a path.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

type Kind = Literal["files", "trials", "report"]
type Which = Literal["kept", "last"] | int

KINDS = ("files", "trials", "report")
"""The kinds of deliverable."""


@dataclass(frozen=True)
class Source:
  """Where a deliverable comes from.

  Attributes:
    state: The state, or a tuple of states, even of one: of their visits,
      the source is the last that has the output, under the same visit
      rule, where a single state's last visit is the source whether it has
      the output or not; a tuple takes an output, and a visit "kept" or
      "last".
    output: The output's name; None for a "trials" deliverable, which takes
      the visit's trials.
    visit: Which visit of the state: "kept" for its last one up to the visit
      the attempt kept (see Attempt.final), its last one when none was kept;
      "last" for its last one; n for its n-th, from 1.
  """

  state: str | tuple[str, ...]
  output: str | None = None
  visit: Which = "kept"

  @property
  def states(self) -> tuple[str, ...]:
    """The state, or the states, as a tuple."""
    return (self.state,) if isinstance(self.state, str) else self.state


@dataclass(frozen=True)
class Value:
  """A value taken from a visit by a list of keys; nothing is evaluated.

  Attributes:
    field: The keys, from the outside in.
    output: A JSON output of the visit to take it from, such as
      "verdict.json"; None for the visit's outcome data.
  """

  field: Sequence[str]
  output: str | None = None


@dataclass(frozen=True)
class Deliverable:
  """One thing an attempt delivers.

  Attributes:
    kind: "files" for an output shown as files, "trials" for the trials of a
      visit, "report" for a Markdown output.
    title: Its title, for people.
    source: Where it comes from.
    table: For a report, a JSON output with the same results as a table, if
      any.
    value: For trials, a value to show with them, if any.
    match: For trials, a glob (fnmatch, case-sensitive) that a trial's output
      name matches to be listed, such as "regrade-[0-9]*"; None for every
      trial of the visit.
    verdicts: For trials, where a JSON object of each trial's verdict, a
      word, by its output name is, if anywhere.
  """

  kind: Kind
  title: str
  source: Source
  table: Source | None = None
  value: Value | None = None
  match: str | None = None
  verdicts: Value | None = None


def check(deliverables: Mapping[str, Deliverable], states: set[str]) -> None:
  """Check that deliverables are well formed.

  Args:
    deliverables: The deliverables, by name.
    states: The names of the machine's states.

  Raises:
    ValueError: When one has an unknown kind, state or visit, has or lacks an
      output against its kind, has a table, a value or a match its kind does
      not, verdicts its kind does not, or an empty match, or a source of
      several states has no output or a numbered visit.
  """
  for name, item in deliverables.items():
    where = f"deliverable {name}"
    if (why := _kind_error(item)) is not None:
      raise ValueError(f"{where}: {why}")
    for source in (item.source, item.table):
      if source is None:
        continue
      if not source.states:
        raise ValueError(f"{where}: no state")
      for state in source.states:
        if state not in states:
          raise ValueError(f"{where}: unknown state {state}")
      if source.visit not in ("kept", "last") and not (
        type(source.visit) is int and source.visit >= 1
      ):
        raise ValueError(f"{where}: unknown visit {source.visit!r}")
      if isinstance(source.state, tuple) and (
        source.output is None or source.visit not in ("kept", "last")
      ):
        why = (
          "a source of several states takes an output and visit kept or last"
        )
        raise ValueError(f"{where}: {why}")


def _kind_error(item: Deliverable) -> str | None:
  if item.kind not in KINDS:
    return f"unknown kind {item.kind!r}"
  if (item.kind == "trials") != (item.source.output is None):
    need = "no output" if item.kind == "trials" else "an output"
    return f"a {item.kind} deliverable takes {need}"
  if item.table is not None and item.kind != "report":
    return "only a report has a table"
  if item.value is not None and item.kind != "trials":
    return "only trials have a value"
  if item.match is not None and item.kind != "trials":
    return "only trials have a match"
  if item.verdicts is not None and item.kind != "trials":
    return "only trials have verdicts"
  if item.match == "":
    return "an empty match"
  return None

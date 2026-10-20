"""What an attempt delivers: declared by name, resolved when it ends.

A dataset declares each deliverable as a Deliverable: its kind, which says how
it is shown ("files" or "report"), a title, and where it comes from,
a state's output in one of the state's visits. The machine
(dataforge.curation.engine) resolves them when an attempt ends into the
record's "deliverables", with paths relative to the attempt's root and, for
what was not reached, a reason instead of a path.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

type Kind = Literal["files", "report"]
type Which = Literal["kept", "last"] | int

KINDS = ("files", "report")
"""The kinds of deliverable."""


@dataclass(frozen=True)
class Source:
  """Where a deliverable comes from.

  Attributes:
    state: The state.
    output: The output's name.
    visit: Which visit of the state: "kept" for its last one up to the visit
      the attempt kept (see Attempt.final), its last one when none was kept;
      "last" for its last one; n for its n-th, from 1.
  """

  state: str
  output: str
  visit: Which = "kept"


@dataclass(frozen=True)
class Deliverable:
  """One thing an attempt delivers.

  Attributes:
    kind: "files" for an output shown as files, "report" for a Markdown
      output.
    title: Its title, for people.
    source: Where it comes from.
    table: For a report, a JSON output with the same results as a table, if
      any.
  """

  kind: Kind
  title: str
  source: Source
  table: Source | None = None


def check(deliverables: Mapping[str, Deliverable], states: set[str]) -> None:
  """Check that deliverables are well formed.

  Args:
    deliverables: The deliverables, by name.
    states: The names of the machine's states.

  Raises:
    ValueError: When one has an unknown kind, state or visit, or a table its
      kind does not have.
  """
  for name, item in deliverables.items():
    where = f"deliverable {name}"
    if item.kind not in KINDS:
      raise ValueError(f"{where}: unknown kind {item.kind!r}")
    if item.table is not None and item.kind != "report":
      raise ValueError(f"{where}: only a report has a table")
    for source in (item.source, item.table):
      if source is None:
        continue
      if source.state not in states:
        raise ValueError(f"{where}: unknown state {source.state}")
      if source.visit not in ("kept", "last") and not (
        type(source.visit) is int and source.visit >= 1
      ):
        raise ValueError(f"{where}: unknown visit {source.visit!r}")

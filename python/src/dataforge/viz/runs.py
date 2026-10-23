"""How an attempt stands, from the store of a project's attempts.

With runs, a project's attempts have their rows in the store of
dataforge.runs, and an attempt's status, reason, cost and whether it runs
come from its row, not from its record file or a lock. The store is opened
with SQLite's read-only URI and never written. A holder that is gone leaves
its row running until a reconcile moves it; the viewer only says so. A row a
person stopped has ended for the viewer, though the store keeps it
interrupted and it may go on.
"""

from dataclasses import dataclass, field
import sqlite3

from dataforge.queue.proc import alive
from dataforge.runs.store import AttemptRow, SCHEMA_MAJOR, Store
from dataforge.viz import live
from dataforge.viz.config import Action, Project, Runs

INTERRUPTED = "interrupted"
"""The status word of an interrupted attempt whose way of being stopped the
project file gives no word for."""

NO_ROW = "the store has no row for this attempt"
"""The listing's error for an attempt directory the store has no row of."""

HOLDER_GONE = "its holder is gone; a reconcile has not ended it yet"
"""What a running row whose holder is gone says."""

NOT_CHECKED = "not checked yet whether it can go on"
"""Why an action with when "stopped" waits for a row whose resumable is
null."""


@dataclass(frozen=True, slots=True)
class Found:
  """The store's rows, or why they could not be read.

  Attributes:
    rows: The rows read, the oldest first; none when error is set.
    error: Why the store was not read, for every attempt; None when it was.
    stopped_at: For each row a person stopped, by ID, when the store last
      made it interrupted: the at of its newest event into interrupted.
  """

  rows: list[AttemptRow]
  error: str | None = None
  stopped_at: dict[str, str] = field(default_factory=dict[str, str])


def read(runs: Runs, item: str | None = None) -> Found:
  """Read the store's rows, read-only.

  A store that does not exist yet has no rows; one whose schema_major is
  newer than this dataforge's is not read.

  Args:
    runs: The project's store.
    item: Only this item's rows; None for every item.

  Returns:
    The rows, or why they were not read.
  """
  store = Store(runs.db)
  if not runs.db.exists():
    return Found([])
  try:
    major, _minor = store.schema()
    if major > SCHEMA_MAJOR:
      return Found(
        [],
        f"the store's schema_major {major} is newer than this viewer's"
        + f" {SCHEMA_MAJOR}",
      )
    rows = store.rows(item=item)
    return Found(
      rows,
      stopped_at={
        row.id: at
        for row in rows
        if (at := _stopped_at(store, row)) is not None
      },
    )
  except (sqlite3.Error, OSError, KeyError, ValueError) as error:
    return Found([], f"the store cannot be read: {error}")


def status(runs: Runs, row: AttemptRow) -> str | None:
  """Give a row's status word.

  Args:
    runs: The project's store, which names the words of an interrupted row.
    row: The row.

  Returns:
    The outcome of an ended row; the word stopped_by names for an
    interrupted one, else INTERRUPTED; None for a running one.
  """
  if row.state == "ended":
    return row.outcome
  if row.state == "interrupted":
    return runs.stopped_by.get(row.stopped_by or "", INTERRUPTED)
  return None


def has_ended(row: AttemptRow) -> bool:
  """Say whether a row has ended for the viewer.

  Args:
    row: The row.

  Returns:
    True for an ended row, and for an interrupted one a person stopped.
  """
  return row.state == "ended" or (
    row.state == "interrupted" and row.stopped_by == "person"
  )


def _stopped_at(store: Store, row: AttemptRow) -> str | None:
  # When the store last made a row a person stopped interrupted; None for
  # any other row, or one with no such event.
  if row.state != "interrupted" or row.stopped_by != "person":
    return None
  return next(
    (
      e.at
      for e in reversed(store.events(row.id))
      if e.to_state == "interrupted"
    ),
    None,
  )


def holder_gone(row: AttemptRow) -> bool:
  """Say whether a running row's holder is no longer alive.

  Args:
    row: The row.

  Returns:
    True for a running row with no holder, or whose holder's pid, starttime
    and boot ID no longer name a live process.
  """
  return row.state == "running" and (
    row.holder is None or not alive(row.holder)
  )


def note(row: AttemptRow, gone: bool) -> str | None:
  """Say what a reconcile has yet to do with a row.

  Args:
    row: The row.
    gone: Whether its holder is gone, for a running row.

  Returns:
    For an interrupted row, or a running one whose holder is gone, that its
    machine ended with its machine_status when it has one, else for the
    running one that its holder is gone; None for any other row.
  """
  stopped = row.state == "interrupted" or (row.state == "running" and gone)
  if not stopped:
    return None
  if row.machine_status is not None:
    return (
      f"its machine ended {row.machine_status}; a reconcile has not ended it"
      + " yet"
    )
  return HOLDER_GONE if row.state == "running" else None


def mark(project: Project, rows: list[dict[str, object]]) -> None:
  """Set each listing row's status, reason, cost and live from the store.

  An attempt with a row takes its status, reason, cost_usd, cost_from, live,
  holder, state, note and progress_at from it, holder true for a running row
  whose holder is alive, live None for a row that has ended (has_ended), and
  for a row a person stopped its ended_at too, from stopped_at, whatever its
  record file holds, and its started_at, created_at, when its record file
  gives none; one with none keeps what its record file gave and says
  so in its error, as does every attempt when the store is not read.

  Args:
    project: The project, with runs.
    rows: The listing.
  """
  runs = project.runs
  if runs is None:
    return
  found = read(runs)
  by_id = {row.id: row for row in found.rows}
  for listed in rows:
    row = by_id.get(str(listed["id"]))
    if row is None:
      why = found.error or NO_ROW
      error = listed["error"]
      listed["error"] = why if error is None else f"{error}; {why}"
      continue
    gone = holder_gone(row)
    listed.update(
      status=status(runs, row),
      reason=row.reason,
      cost_usd=row.cost_usd,
      cost_from=None if row.cost_usd is None else "store",
      live=None
      if has_ended(row)
      else live.MAY_HAVE_STOPPED
      if row.state == "interrupted" or gone
      else live.RUNNING,
      holder=row.state == "running" and not gone,
      state=row.state,
      note=note(row, gone),
      progress_at=row.progress_at,
    )
    if row.state == "interrupted" and has_ended(row):
      listed["ended_at"] = found.stopped_at.get(row.id, listed["ended_at"])
      # Stopped before its record had a visit, it starts when its row was
      # made, so that the page does not take it as its item's oldest.
      if listed["started_at"] is None:
        listed["started_at"] = row.created_at


def unavailable(
  project: Project, action: Action, item: str, found: Found
) -> str | None:
  """Say why an action may not be submitted for an item, by its rows.

  Args:
    project: The project, whose statuses label how a row ended.
    action: The action, whose when decides.
    item: The item's ID.
    found: The item's rows in the store, the oldest first.

  Returns:
    The reason, for a person; None when the rows let it be submitted: with
    when "idle", no row of the item is running; with "stopped", the item's
    newest row is interrupted, its machine_status null and resumable true.
  """
  when = action.when
  if when is None:
    return None
  if found.error is not None:
    return f"The store of attempts cannot be read: {found.error}."
  if when == "idle":
    if any(row.state == "running" for row in found.rows):
      return "An attempt of this item is running."
    return None
  if not found.rows:
    return "The store has no attempt of this item."
  return _not_going_on(project, action, item, found.rows[-1])


def _not_going_on(
  project: Project, action: Action, item: str, newest: AttemptRow
) -> str | None:
  # Why the item's newest row may not go on, or None when it may.
  if newest.state == "running":
    return "The newest attempt of this item is running."
  if newest.state == "ended":
    return _ended(project, action, item, newest)
  if newest.machine_status is not None:
    return _sentence(note(newest, False) or "")
  if newest.resumable is None:
    return _sentence(NOT_CHECKED)
  if not newest.resumable:
    return newest.resume_problem or "It cannot go on."
  return None


def _ended(
  project: Project, action: Action, item: str, newest: AttemptRow
) -> str:
  # Why an action with when "stopped" may not go on with the item's newest
  # row, which has ended: what it needs, and how that one ended and why.
  word = newest.outcome
  label = (
    "with no status"
    if word is None
    else project.statuses.get(word, {}).get("label", word)
  )
  why = "" if not newest.reason else f": {newest.reason.rstrip('.')}"
  stamp = newest.id.removeprefix(f"{item}/")
  return (
    f"{action.label} needs an attempt that was interrupted and can go on;"
    + f" the newest, {stamp}, ended {label}{why}."
  )


def _sentence(words: str) -> str:
  # The viewer's own words as a reason: a sentence, as the other reasons are.
  return f"{words[:1].upper()}{words[1:]}."
